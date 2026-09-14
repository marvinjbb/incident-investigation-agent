import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid4

from app.config import Settings
from app.evidence import EvidenceItem, EvidenceType
from app.investigation.models import InvestigationRecord
from app.lab import IncidentLab, RemediationPreconditionError, WorkloadUnavailableError
from app.logging_config import log_event
from app.models import IncidentStatus, ScenarioType
from app.remediation.models import (
    RemediationAction,
    RemediationProposal,
    RemediationStatus,
)
from app.tools.database import PostgreSQLDiagnosticTool

logger = logging.getLogger(__name__)


class InvestigationReader(Protocol):
    async def get(self, investigation_id: UUID) -> InvestigationRecord | None: ...


class ProposalStore(Protocol):
    async def create(self, proposal: RemediationProposal) -> None: ...
    async def get(self, proposal_id: UUID) -> RemediationProposal | None: ...
    async def list_for_incident(
        self, incident_id: UUID, limit: int = 20
    ) -> list[RemediationProposal]: ...
    async def transition(
        self,
        proposal_id: UUID,
        expected: RemediationStatus,
        target: RemediationStatus,
        event_type: str,
        *,
        execution_result: dict[str, object] | None = None,
        verification_result: dict[str, object] | None = None,
    ) -> bool: ...
    async def add_audit(
        self, proposal_id: UUID, event_type: str, details: dict[str, object]
    ) -> None: ...


class RemediationError(RuntimeError):
    pass


class ProposalNotFoundError(RemediationError):
    pass


class ProposalPolicyError(RemediationError):
    pass


class ProposalStateError(RemediationError):
    pass


class ProposalExpiredError(RemediationError):
    pass


class RemediationExecutionError(RemediationError):
    pass


ACTION_POLICY = {
    ScenarioType.BLOCKED_QUERY: (
        RemediationAction.TERMINATE_DEMO_BLOCKER,
        {EvidenceType.BLOCKED_SESSION},
        "Terminate the controlled blocking database session",
    ),
    ScenarioType.CONNECTION_EXHAUSTION: (
        RemediationAction.RELEASE_DEMO_POOL_PRESSURE,
        {EvidenceType.POOL_STATE, EvidenceType.CONNECTION_UTILIZATION},
        "Release the controlled application pool pressure",
    ),
    ScenarioType.BAD_DEPLOYMENT: (
        RemediationAction.ROLLBACK_DEMO_DEPLOYMENT,
        {EvidenceType.DEPLOYMENT_CHANGE, EvidenceType.APPLICATION_ERROR},
        "Roll back the controlled bad deployment to the healthy release",
    ),
}


class RemediationService:
    def __init__(
        self,
        settings: Settings,
        lab: IncidentLab,
        investigations: InvestigationReader,
        proposals: ProposalStore,
        postgresql: PostgreSQLDiagnosticTool,
    ) -> None:
        self._settings = settings
        self._lab = lab
        self._investigations = investigations
        self._proposals = proposals
        self._postgresql = postgresql

    async def propose(self, investigation_id: UUID) -> RemediationProposal:
        investigation = await self._investigations.get(investigation_id)
        if (
            investigation is None
            or investigation.status != "completed"
            or investigation.report is None
        ):
            raise ProposalPolicyError("A completed investigation is required")
        report = investigation.report
        incident = await self._lab.get_incident(report.incident_id)
        if incident.status is not IncidentStatus.ACTIVE:
            raise ProposalPolicyError("The incident is no longer active")
        if not any(action.approval_required for action in report.recommended_actions):
            raise ProposalPolicyError("No eligible recommendation is available")

        action_type, required_types, summary = ACTION_POLICY[incident.scenario]
        catalog = {item.evidence_id: item for item in report.evidence_catalog}
        if not report.cited_evidence_ids().issubset(catalog):
            raise ProposalPolicyError("Investigation citations are invalid")
        supporting = [
            item.evidence_id
            for item in report.evidence_catalog
            if item.evidence_type in required_types
            and item.evidence_id in report.cited_evidence_ids()
        ]
        present_types = {catalog[item].evidence_type for item in supporting}
        if not required_types.issubset(present_types):
            raise ProposalPolicyError("Required remediation evidence is missing")
        self._validate_evidence_ownership(action_type, catalog, supporting)

        now = datetime.now(UTC)
        proposal = RemediationProposal(
            proposal_id=uuid4(),
            incident_id=incident.incident_id,
            investigation_id=investigation_id,
            action_type=action_type,
            status=RemediationStatus.PENDING_APPROVAL,
            summary=summary,
            reason=report.primary_hypothesis.cause,
            supporting_evidence_ids=supporting[:10],
            created_at=now,
            expires_at=now
            + timedelta(seconds=self._settings.remediation_proposal_ttl_seconds),
        )
        await self._proposals.create(proposal)
        log_event(
            logger,
            "remediation_proposal_created",
            "Remediation proposal created",
            proposal_id=proposal.proposal_id,
            incident_id=proposal.incident_id,
            investigation_id=proposal.investigation_id,
            action_type=proposal.action_type.value,
        )
        return await self._require_proposal(proposal.proposal_id)

    async def get(self, proposal_id: UUID) -> RemediationProposal:
        return await self._require_proposal(proposal_id)

    async def list_for_incident(self, incident_id: UUID) -> list[RemediationProposal]:
        await self._lab.get_incident(incident_id)
        return await self._proposals.list_for_incident(incident_id)

    async def approve(self, proposal_id: UUID) -> RemediationProposal:
        proposal = await self._require_current(proposal_id)
        if proposal.status is not RemediationStatus.PENDING_APPROVAL:
            raise ProposalStateError("Proposal is not pending approval")
        changed = await self._proposals.transition(
            proposal_id,
            RemediationStatus.PENDING_APPROVAL,
            RemediationStatus.APPROVED,
            "remediation_approved",
        )
        if not changed:
            raise ProposalStateError("Proposal state changed")
        log_event(
            logger,
            "remediation_approved",
            "Human approval recorded",
            proposal_id=proposal_id,
            incident_id=proposal.incident_id,
            action_type=proposal.action_type.value,
        )
        return await self._require_proposal(proposal_id)

    async def reject(self, proposal_id: UUID) -> RemediationProposal:
        proposal = await self._require_current(proposal_id)
        if proposal.status is not RemediationStatus.PENDING_APPROVAL:
            raise ProposalStateError("Proposal is not pending approval")
        changed = await self._proposals.transition(
            proposal_id,
            RemediationStatus.PENDING_APPROVAL,
            RemediationStatus.REJECTED,
            "remediation_rejected",
        )
        if not changed:
            raise ProposalStateError("Proposal state changed")
        log_event(
            logger,
            "remediation_rejected",
            "Human rejection recorded",
            proposal_id=proposal_id,
            incident_id=proposal.incident_id,
            action_type=proposal.action_type.value,
        )
        return await self._require_proposal(proposal_id)

    async def execute(self, proposal_id: UUID) -> RemediationProposal:
        proposal = await self._require_proposal(proposal_id)
        if proposal.status is RemediationStatus.SUCCEEDED:
            return proposal
        proposal = await self._require_current(proposal_id)
        if proposal.status is not RemediationStatus.APPROVED:
            raise ProposalStateError("Explicit approval is required")
        try:
            await self._validate_policy(proposal)
        except ProposalPolicyError:
            await self._proposals.transition(
                proposal_id,
                RemediationStatus.APPROVED,
                RemediationStatus.FAILED,
                "remediation_precondition_failed",
                execution_result={"outcome": "stale_precondition"},
            )
            raise
        changed = await self._proposals.transition(
            proposal_id,
            RemediationStatus.APPROVED,
            RemediationStatus.EXECUTING,
            "remediation_execution_started",
        )
        if not changed:
            raise ProposalStateError("Proposal is already being executed")
        started = monotonic()
        log_event(
            logger,
            "remediation_started",
            "Allowlisted remediation started",
            proposal_id=proposal_id,
            incident_id=proposal.incident_id,
            action_type=proposal.action_type.value,
        )
        try:
            await self._execute_action(proposal)
            duration_ms = int((monotonic() - started) * 1000)
            await self._proposals.add_audit(
                proposal_id,
                "remediation_execution_succeeded",
                {"action_type": proposal.action_type.value},
            )
            await self._proposals.add_audit(
                proposal_id,
                "remediation_verification_started",
                {"action_type": proposal.action_type.value},
            )
            verification = await self._verify(proposal)
            if not all(verification.values()):
                await self._lab.store.update_status(
                    proposal.incident_id, IncidentStatus.FAILED
                )
                raise RemediationExecutionError("Recovery verification failed")
            await self._proposals.add_audit(
                proposal_id,
                "remediation_verification_completed",
                {
                    "action_type": proposal.action_type.value,
                    "verified": True,
                },
            )
            await self._proposals.transition(
                proposal_id,
                RemediationStatus.EXECUTING,
                RemediationStatus.SUCCEEDED,
                "remediation_completed",
                execution_result={"outcome": "completed", "duration_ms": duration_ms},
                verification_result=verification,
            )
            log_event(
                logger,
                "remediation_completed",
                "Remediation completed and verified",
                proposal_id=proposal_id,
                incident_id=proposal.incident_id,
                action_type=proposal.action_type.value,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            await self._proposals.transition(
                proposal_id,
                RemediationStatus.EXECUTING,
                RemediationStatus.FAILED,
                "remediation_execution_failed",
                execution_result={
                    "outcome": "failed",
                    "error_type": type(exc).__name__,
                },
            )
            log_event(
                logger,
                "remediation_failed",
                "Remediation failed safely",
                proposal_id=proposal_id,
                incident_id=proposal.incident_id,
                action_type=proposal.action_type.value,
                error_type=type(exc).__name__,
            )
            raise RemediationExecutionError("Remediation failed safely") from exc
        return await self._require_proposal(proposal_id)

    async def _validate_policy(self, proposal: RemediationProposal) -> None:
        investigation = await self._investigations.get(proposal.investigation_id)
        if investigation is None or investigation.report is None:
            raise ProposalPolicyError("Investigation is unavailable")
        report = investigation.report
        if report.incident_id != proposal.incident_id:
            raise ProposalPolicyError("Proposal incident does not match investigation")
        incident = await self._lab.get_incident(proposal.incident_id)
        expected_action, required_types, _ = ACTION_POLICY[incident.scenario]
        if proposal.action_type is not expected_action:
            raise ProposalPolicyError("Action does not match incident policy")
        catalog = {item.evidence_id: item for item in report.evidence_catalog}
        if not set(proposal.supporting_evidence_ids).issubset(catalog):
            raise ProposalPolicyError("Proposal contains unknown evidence")
        cited_types = {
            catalog[item].evidence_type for item in proposal.supporting_evidence_ids
        }
        if not required_types.issubset(cited_types):
            raise ProposalPolicyError("Proposal evidence no longer satisfies policy")
        self._validate_evidence_ownership(
            proposal.action_type, catalog, proposal.supporting_evidence_ids
        )
        if incident.status is not IncidentStatus.ACTIVE:
            raise ProposalPolicyError("Incident is no longer active")

    def _validate_evidence_ownership(
        self,
        action_type: RemediationAction,
        catalog: dict[str, EvidenceItem],
        supporting_ids: list[str],
    ) -> None:
        if action_type is not RemediationAction.TERMINATE_DEMO_BLOCKER:
            return
        owned = any(
            catalog[item].details.get("blocking_application_name")
            == "incident-demo-lock-holder"
            and catalog[item].details.get("blocked_application_name")
            == "incident-demo-blocked-query"
            and isinstance(catalog[item].details.get("blocking_pid"), int)
            for item in supporting_ids
        )
        if not owned:
            raise ProposalPolicyError("Blocking evidence ownership is unproven")

    async def _execute_action(self, proposal: RemediationProposal) -> None:
        actions = {
            RemediationAction.TERMINATE_DEMO_BLOCKER: self._lab.terminate_demo_blocker,
            RemediationAction.RELEASE_DEMO_POOL_PRESSURE: (
                self._lab.release_demo_pool_pressure
            ),
            RemediationAction.ROLLBACK_DEMO_DEPLOYMENT: (
                self._lab.rollback_demo_deployment
            ),
        }
        try:
            await actions[proposal.action_type](proposal.incident_id)
        except RemediationPreconditionError as exc:
            raise ProposalPolicyError("Remediation preconditions changed") from exc

    async def _verify(self, proposal: RemediationProposal) -> dict[str, bool]:
        incident = await self._lab.get_incident(proposal.incident_id)
        checks = {"incident_resolved": incident.status is IncidentStatus.RESOLVED}
        if proposal.action_type is RemediationAction.TERMINATE_DEMO_BLOCKER:
            checks[
                "blocking_relationship_removed"
            ] = not await self._postgresql.blocking_relationships(proposal.incident_id)
        elif proposal.action_type is RemediationAction.RELEASE_DEMO_POOL_PRESSURE:
            checks["pool_available"] = self._lab.pool_state().available > 0
        else:
            checks["healthy_release_active"] = (
                await self._lab.store.active_version() == "v1"
            )
        try:
            await self._lab.workload("/demo/workload")
            checks["workload_healthy"] = True
        except WorkloadUnavailableError:
            checks["workload_healthy"] = False
        return checks

    async def _require_current(self, proposal_id: UUID) -> RemediationProposal:
        proposal = await self._require_proposal(proposal_id)
        if (
            proposal.status
            in {
                RemediationStatus.PENDING_APPROVAL,
                RemediationStatus.APPROVED,
            }
            and datetime.now(UTC) >= proposal.expires_at
        ):
            await self._proposals.transition(
                proposal_id,
                proposal.status,
                RemediationStatus.EXPIRED,
                "remediation_expired",
            )
            raise ProposalExpiredError("Remediation proposal expired")
        return proposal

    async def _require_proposal(self, proposal_id: UUID) -> RemediationProposal:
        proposal = await self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError("Remediation proposal not found")
        return proposal
