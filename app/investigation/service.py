import asyncio
import logging
import re
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.config import Settings
from app.evidence import EvidenceItem, EvidenceSource
from app.investigation.models import (
    InvestigationMetrics,
    InvestigationReport,
    ReportDraft,
    ToolActivity,
)
from app.investigation.provider import (
    InvestigationConfigurationError,
    InvestigationProvider,
    InvestigationProviderError,
    evidence_tool_output,
)
from app.investigation.registry import InvestigationToolRegistry
from app.logging_config import log_event

logger = logging.getLogger(__name__)

SAFE_EVIDENCE_ID = re.compile(r"^ev_[0-9a-f]{20}$")


class InvestigationPersistence(Protocol):
    async def start(
        self, investigation_id: UUID, incident_id: UUID, model: str
    ) -> None: ...
    async def complete(
        self, investigation_id: UUID, report: InvestigationReport
    ) -> None: ...
    async def fail(
        self, investigation_id: UUID, error_type: str, trace: list[ToolActivity]
    ) -> None: ...


class InvestigationError(RuntimeError):
    pass


class InvestigationBudgetExceeded(InvestigationError):
    pass


class InvalidInvestigationReport(InvestigationError):
    def __init__(self, code: str, record_ids: set[str] | None = None) -> None:
        super().__init__("Investigation report failed grounding validation")
        self.code = code
        self.record_ids = sorted(record_ids or set())


class DiagnosticToolFailure(InvestigationError):
    pass


class IncidentInvestigator:
    def __init__(
        self,
        settings: Settings,
        provider: InvestigationProvider,
        registry_factory: Any,
        store: InvestigationPersistence,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._registry_factory = registry_factory
        self._store = store

    async def investigate(self, incident_id: UUID) -> InvestigationReport:
        registry: InvestigationToolRegistry = self._registry_factory(incident_id)
        investigation_id = uuid4()
        started = monotonic()
        trace: list[ToolActivity] = []
        evidence: dict[str, EvidenceItem] = {}
        model_calls = 0
        tool_calls = 0
        repair_attempted = False
        await registry.validate_incident()
        await self._store.start(
            investigation_id, incident_id, self._settings.openai_model
        )
        log_event(
            logger,
            "investigation_started",
            "Investigation started",
            incident_id=incident_id,
            investigation_id=investigation_id,
        )
        history: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Investigate incident {incident_id}. Select approved diagnostics "
                    "before forming an evidence-backed report."
                ),
            }
        ]
        try:
            for _ in range(self._settings.investigation_max_iterations):
                model_calls += 1
                turn = await asyncio.wait_for(
                    self._provider.respond(history, registry.definitions),
                    timeout=self._settings.investigation_timeout_seconds,
                )
                history.extend(turn.output_items)
                if turn.tool_requests:
                    if (
                        tool_calls + len(turn.tool_requests)
                        > self._settings.investigation_max_tool_calls
                    ):
                        raise InvestigationBudgetExceeded(
                            "Investigation tool budget exceeded"
                        )
                    for request in turn.tool_requests:
                        tool_calls += 1
                        log_event(
                            logger,
                            "model_requested_tool",
                            "Model requested diagnostic tool",
                            incident_id=incident_id,
                            investigation_id=investigation_id,
                            tool=request.name,
                        )
                        try:
                            result = await registry.execute(
                                request.name, request.arguments
                            )
                        except Exception as exc:
                            trace.append(
                                ToolActivity(
                                    tool=request.name,
                                    status="failed",
                                    timestamp=datetime.now(UTC),
                                    result_count=0,
                                )
                            )
                            log_event(
                                logger,
                                "diagnostic_tool_failed",
                                "Diagnostic tool failed",
                                incident_id=incident_id,
                                investigation_id=investigation_id,
                                tool=request.name,
                                error_type=type(exc).__name__,
                            )
                            raise DiagnosticToolFailure(
                                "Diagnostic tool failed"
                            ) from exc
                        evidence.update((item.evidence_id, item) for item in result)
                        activity = ToolActivity(
                            tool=request.name,
                            status="completed",
                            timestamp=datetime.now(UTC),
                            result_count=len(result),
                        )
                        trace.append(activity)
                        log_event(
                            logger,
                            "diagnostic_tool_completed",
                            "Diagnostic tool completed",
                            incident_id=incident_id,
                            investigation_id=investigation_id,
                            tool=request.name,
                            result_count=len(result),
                        )
                        history.append(
                            {
                                "type": "function_call_output",
                                "call_id": request.call_id,
                                "output": evidence_tool_output(
                                    [item.model_dump(mode="json") for item in result]
                                ),
                            }
                        )
                    continue
                if turn.report is None:
                    raise InvestigationProviderError("AI provider returned no report")
                try:
                    report = self._finalize(
                        turn.report,
                        investigation_id,
                        incident_id,
                        evidence,
                        trace,
                        model_calls,
                        tool_calls,
                        started,
                    )
                except InvalidInvestigationReport as exc:
                    if repair_attempted:
                        raise
                    repair_attempted = True
                    log_event(
                        logger,
                        "investigation_report_repair_requested",
                        "Investigation report correction requested",
                        incident_id=incident_id,
                        investigation_id=investigation_id,
                        validation_error_code=exc.code,
                        record_ids=[
                            item
                            for item in exc.record_ids
                            if SAFE_EVIDENCE_ID.fullmatch(item)
                        ],
                    )
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "The draft failed application validation with code "
                                f"{exc.code}. Each citation field must contain only "
                                "exact application-owned IDs beginning with `ev_`, "
                                "never factual sentences, summaries, or paraphrases. "
                                "Use only these exact "
                                f"collected evidence IDs: {sorted(evidence)}. Return a "
                                "corrected report; do not change evidence."
                            ),
                        }
                    )
                    continue
                await self._store.complete(investigation_id, report)
                log_event(
                    logger,
                    "investigation_completed",
                    "Investigation completed",
                    incident_id=incident_id,
                    investigation_id=investigation_id,
                    duration_ms=report.metrics.duration_ms,
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                )
                return report
            raise InvestigationBudgetExceeded("Investigation iteration budget exceeded")
        except (
            TimeoutError,
            InvestigationError,
            InvestigationProviderError,
            InvestigationConfigurationError,
        ) as exc:
            await self._store.fail(investigation_id, type(exc).__name__, trace)
            log_event(
                logger,
                "investigation_failed",
                "Investigation failed safely",
                incident_id=incident_id,
                investigation_id=investigation_id,
                error_type=type(exc).__name__,
                validation_error_code=getattr(exc, "code", None),
            )
            raise

    def _finalize(
        self,
        draft: ReportDraft,
        investigation_id: UUID,
        incident_id: UUID,
        evidence: dict[str, EvidenceItem],
        trace: list[ToolActivity],
        model_calls: int,
        tool_calls: int,
        started: float,
    ) -> InvestigationReport:
        unknown = draft.cited_evidence_ids() - set(evidence)
        if unknown:
            raise InvalidInvestigationReport("unknown_evidence", unknown)
        runbook_refs = {
            item.reference
            for item in evidence.values()
            if item.source is EvidenceSource.RUNBOOK
        }
        if not set(draft.runbook_references).issubset(runbook_refs):
            raise InvalidInvestigationReport(
                "unknown_runbook", set(draft.runbook_references) - runbook_refs
            )
        mutating_words = (
            "terminate",
            "rollback",
            "increase",
            "change",
            "restart",
            "apply",
        )
        for action in draft.recommended_actions:
            if (
                any(word in action.action.lower() for word in mutating_words)
                and not action.approval_required
            ):
                raise InvalidInvestigationReport("approval_required")
        metadata = next(
            (
                item
                for item in evidence.values()
                if item.source is EvidenceSource.INCIDENT
            ),
            None,
        )
        if metadata is None:
            raise InvalidInvestigationReport("missing_metadata")
        return InvestigationReport(
            **draft.model_dump(),
            investigation_id=investigation_id,
            incident_id=incident_id,
            incident_status=str(metadata.details["status"]),
            generated_at=datetime.now(UTC),
            model=self._settings.openai_model,
            activity_trace=trace,
            evidence_catalog=list(evidence.values()),
            metrics=InvestigationMetrics(
                model_calls=model_calls,
                tool_calls=tool_calls,
                duration_ms=int((monotonic() - started) * 1000),
            ),
        )
