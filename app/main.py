import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import get_settings
from app.database import DatabaseCheck, database, get_database_check
from app.evidence import EvidenceBundle
from app.investigation.models import InvestigationRecord, InvestigationReport
from app.investigation.provider import (
    InvestigationConfigurationError,
    InvestigationProviderError,
    OpenAIInvestigationProvider,
)
from app.investigation.registry import InvestigationToolRegistry
from app.investigation.service import (
    DiagnosticToolFailure,
    IncidentInvestigator,
    InvalidInvestigationReport,
    InvestigationBudgetExceeded,
)
from app.investigation.store import InvestigationAlreadyRunningError, InvestigationStore
from app.lab import (
    ActiveIncidentError,
    IncidentLab,
    IncidentNotFoundError,
    WorkloadUnavailableError,
    build_incident_lab,
)
from app.logging_config import configure_logging, log_event
from app.models import (
    Deployment,
    Incident,
    IncidentDetail,
    IncidentStartRequest,
    PoolState,
    ScenarioType,
    WorkloadResponse,
)
from app.public.models import (
    PublicActivity,
    PublicErrorCode,
    PublicEvidence,
    PublicIncident,
    PublicIncidentRequest,
    PublicInvestigation,
    PublicInvestigationReport,
    PublicRemediation,
)
from app.public.store import DemoSessionExpiredError, PublicRateLimitError, PublicStore
from app.remediation.models import RemediationProposal
from app.remediation.service import (
    ProposalExpiredError,
    ProposalNotFoundError,
    ProposalPolicyError,
    ProposalStateError,
    RemediationExecutionError,
    RemediationService,
)
from app.remediation.store import RemediationStore
from app.tools.collector import IncidentEvidenceCollector
from app.tools.database import (
    ApplicationPoolDiagnosticTool,
    PostgreSQLDiagnosticTool,
)
from app.tools.deployments import DeploymentDiagnosticTool
from app.tools.incidents import IncidentEventTool, IncidentMetadataTool
from app.tools.logs import ApplicationLogDiagnosticTool
from app.tools.runbooks import RunbookDiagnosticTool

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)
incident_lab = build_incident_lab(database, settings)
evidence_collector = IncidentEvidenceCollector(
    metadata=IncidentMetadataTool(incident_lab.store),
    events=IncidentEventTool(incident_lab.store, settings.diagnostic_result_limit),
    postgresql=PostgreSQLDiagnosticTool(database, settings.diagnostic_result_limit),
    pool=ApplicationPoolDiagnosticTool(database, settings),
    deployments=DeploymentDiagnosticTool(
        incident_lab.store, settings.diagnostic_result_limit
    ),
    logs=ApplicationLogDiagnosticTool(settings),
    runbooks=RunbookDiagnosticTool(),
    maximum_items=settings.evidence_result_limit,
)
investigation_store = InvestigationStore(database)
remediation_store = RemediationStore(database)
public_store = PublicStore(database, settings.demo_session_ttl_seconds)


def build_tool_registry(incident_id: UUID) -> InvestigationToolRegistry:
    return InvestigationToolRegistry(
        incident_id=incident_id,
        metadata=IncidentMetadataTool(incident_lab.store),
        events=IncidentEventTool(incident_lab.store, settings.diagnostic_result_limit),
        postgresql=PostgreSQLDiagnosticTool(database, settings.diagnostic_result_limit),
        pool=ApplicationPoolDiagnosticTool(database, settings),
        deployments=DeploymentDiagnosticTool(
            incident_lab.store, settings.diagnostic_result_limit
        ),
        logs=ApplicationLogDiagnosticTool(settings),
        runbooks=RunbookDiagnosticTool(),
    )


investigator = IncidentInvestigator(
    settings=settings,
    provider=OpenAIInvestigationProvider(settings),
    registry_factory=build_tool_registry,
    store=investigation_store,
)
remediation_service = RemediationService(
    settings=settings,
    lab=incident_lab,
    investigations=investigation_store,
    proposals=remediation_store,
    postgresql=PostgreSQLDiagnosticTool(database, settings.diagnostic_result_limit),
)


class HealthResponse(BaseModel):
    status: str


class DatabaseHealthResponse(BaseModel):
    status: str
    database: str


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await database.open()
    await database.recover_stale_state()
    await public_store.cleanup()
    try:
        yield
    finally:
        await incident_lab.shutdown()
        await database.close()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Request-ID"],
)


def get_incident_lab() -> IncidentLab:
    return incident_lab


def get_evidence_collector() -> IncidentEvidenceCollector:
    return evidence_collector


def get_investigator() -> IncidentInvestigator:
    return investigator


def get_investigation_store() -> InvestigationStore:
    return investigation_store


def get_remediation_service() -> RemediationService:
    return remediation_service


@app.middleware("http")
async def log_request(request: Request, call_next) -> Response:
    supplied_request_id = request.headers.get("x-request-id")
    try:
        request_id = (
            str(UUID(supplied_request_id)) if supplied_request_id else str(uuid4())
        )
    except ValueError:
        request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    if (
        settings.environment == "production"
        and not settings.expose_internal_routes
        and request.url.path.startswith("/demo/")
    ):
        response = Response(status_code=404)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    log_event(
        logger,
        "request_completed",
        "HTTP request completed",
        path=request.url.path,
        request_id=request_id,
        duration_ms=int((time.perf_counter() - started) * 1000),
        status=response.status_code,
    )
    return response


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/health/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/health/db", response_model=DatabaseHealthResponse)
async def database_health(
    database_check: Annotated[DatabaseCheck, Depends(get_database_check)],
) -> DatabaseHealthResponse:
    try:
        await database_check()
    except Exception as exc:
        logger.warning(
            "Database health check failed",
            extra={
                "event_type": "database_health_failed",
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc
    return DatabaseHealthResponse(status="ok", database="reachable")


@app.get("/health/ready", response_model=DatabaseHealthResponse)
async def readiness(
    database_check: Annotated[DatabaseCheck, Depends(get_database_check)],
) -> DatabaseHealthResponse:
    return await database_health(database_check)


async def start_incident(
    scenario: ScenarioType,
    request: IncidentStartRequest,
    lab: IncidentLab,
) -> Incident:
    duration = request.duration_seconds or settings.incident_default_duration_seconds
    if duration > settings.incident_max_duration_seconds:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "duration_seconds cannot exceed "
                f"{settings.incident_max_duration_seconds}"
            ),
        )
    try:
        return await lab.start(scenario, duration)
    except ActiveIncidentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@app.post(
    "/demo/incidents/blocked-query",
    response_model=Incident,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_blocked_query(
    request: IncidentStartRequest,
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> Incident:
    return await start_incident(ScenarioType.BLOCKED_QUERY, request, lab)


@app.post(
    "/demo/incidents/connection-exhaustion",
    response_model=Incident,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_connection_exhaustion(
    request: IncidentStartRequest,
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> Incident:
    return await start_incident(ScenarioType.CONNECTION_EXHAUSTION, request, lab)


@app.post(
    "/demo/incidents/bad-deployment",
    response_model=Incident,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_bad_deployment(
    request: IncidentStartRequest,
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> Incident:
    return await start_incident(ScenarioType.BAD_DEPLOYMENT, request, lab)


@app.get("/demo/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: UUID,
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> IncidentDetail:
    try:
        return await lab.get_incident(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@app.get(
    "/demo/incidents/{incident_id}/evidence",
    response_model=EvidenceBundle,
)
async def get_incident_evidence(
    incident_id: UUID,
    collector: Annotated[IncidentEvidenceCollector, Depends(get_evidence_collector)],
) -> EvidenceBundle:
    try:
        return await collector.collect(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found"
        ) from exc


@app.post(
    "/demo/incidents/{incident_id}/investigate",
    response_model=InvestigationReport,
)
async def investigate_incident(
    incident_id: UUID,
    service: Annotated[IncidentInvestigator, Depends(get_investigator)],
) -> InvestigationReport:
    try:
        return await service.investigate(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc
    except InvestigationConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="AI investigation is not configured"
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail="AI investigation timed out"
        ) from exc
    except InvestigationAlreadyRunningError as exc:
        raise HTTPException(
            status_code=409, detail="An investigation is already running"
        ) from exc
    except (
        InvestigationProviderError,
        InvestigationBudgetExceeded,
        InvalidInvestigationReport,
        DiagnosticToolFailure,
    ) as exc:
        raise HTTPException(
            status_code=502, detail="Investigation failed safely"
        ) from exc


@app.get(
    "/demo/investigations/{investigation_id}",
    response_model=InvestigationRecord,
)
async def get_investigation(
    investigation_id: UUID,
    store: Annotated[InvestigationStore, Depends(get_investigation_store)],
) -> InvestigationRecord:
    record = await store.get(investigation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return record


@app.get(
    "/demo/incidents/{incident_id}/investigations",
    response_model=list[InvestigationRecord],
)
async def list_incident_investigations(
    incident_id: UUID,
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
    store: Annotated[InvestigationStore, Depends(get_investigation_store)],
) -> list[InvestigationRecord]:
    try:
        await lab.get_incident(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc
    return await store.list_for_incident(incident_id)


def remediation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProposalNotFoundError):
        return HTTPException(status_code=404, detail="Remediation proposal not found")
    if isinstance(exc, ProposalExpiredError):
        return HTTPException(status_code=410, detail="Remediation proposal expired")
    if isinstance(exc, (ProposalPolicyError, ProposalStateError)):
        return HTTPException(status_code=409, detail="Remediation cannot proceed")
    return HTTPException(status_code=502, detail="Remediation failed safely")


@app.post(
    "/demo/investigations/{investigation_id}/remediation-proposals",
    response_model=RemediationProposal,
    status_code=status.HTTP_201_CREATED,
)
async def create_remediation_proposal(
    investigation_id: UUID,
    service: Annotated[RemediationService, Depends(get_remediation_service)],
) -> RemediationProposal:
    try:
        return await service.propose(investigation_id)
    except (ProposalNotFoundError, ProposalPolicyError) as exc:
        raise remediation_error(exc) from exc


@app.get(
    "/demo/incidents/{incident_id}/remediations",
    response_model=list[RemediationProposal],
)
async def list_incident_remediations(
    incident_id: UUID,
    service: Annotated[RemediationService, Depends(get_remediation_service)],
) -> list[RemediationProposal]:
    try:
        return await service.list_for_incident(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc


@app.get("/demo/remediations/{proposal_id}", response_model=RemediationProposal)
async def get_remediation(
    proposal_id: UUID,
    service: Annotated[RemediationService, Depends(get_remediation_service)],
) -> RemediationProposal:
    try:
        return await service.get(proposal_id)
    except ProposalNotFoundError as exc:
        raise remediation_error(exc) from exc


@app.post(
    "/demo/remediations/{proposal_id}/approve", response_model=RemediationProposal
)
async def approve_remediation(
    proposal_id: UUID,
    service: Annotated[RemediationService, Depends(get_remediation_service)],
) -> RemediationProposal:
    try:
        return await service.approve(proposal_id)
    except (ProposalNotFoundError, ProposalExpiredError, ProposalStateError) as exc:
        raise remediation_error(exc) from exc


@app.post("/demo/remediations/{proposal_id}/reject", response_model=RemediationProposal)
async def reject_remediation(
    proposal_id: UUID,
    service: Annotated[RemediationService, Depends(get_remediation_service)],
) -> RemediationProposal:
    try:
        return await service.reject(proposal_id)
    except (ProposalNotFoundError, ProposalExpiredError, ProposalStateError) as exc:
        raise remediation_error(exc) from exc


@app.post(
    "/demo/remediations/{proposal_id}/execute", response_model=RemediationProposal
)
async def execute_remediation(
    proposal_id: UUID,
    service: Annotated[RemediationService, Depends(get_remediation_service)],
) -> RemediationProposal:
    try:
        return await service.execute(proposal_id)
    except (
        ProposalNotFoundError,
        ProposalExpiredError,
        ProposalPolicyError,
        ProposalStateError,
        RemediationExecutionError,
    ) as exc:
        raise remediation_error(exc) from exc


@app.post("/demo/incidents/{incident_id}/recover", response_model=IncidentDetail)
async def recover_incident(
    incident_id: UUID,
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> IncidentDetail:
    try:
        return await lab.recover(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@app.get("/demo/workload", response_model=WorkloadResponse)
async def demo_workload(
    request: Request,
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> WorkloadResponse:
    try:
        return await lab.workload(request.url.path)
    except WorkloadUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo workload is temporarily unavailable",
        ) from exc


@app.get("/demo/deployments", response_model=list[Deployment])
async def deployment_history(
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> list[Deployment]:
    return await lab.deployments()


@app.get("/demo/diagnostics/pool", response_model=PoolState)
async def pool_state(
    lab: Annotated[IncidentLab, Depends(get_incident_lab)],
) -> PoolState:
    return lab.pool_state()


SESSION_COOKIE = "incident_demo_session"
PUBLIC_EVENT_LABELS = {
    "incident_started": "Incident created",
    "lock_acquired": "Database lock detected",
    "query_blocked": "Blocked query confirmed",
    "pool_saturated": "Application connection pool saturated",
    "request_failed": "Application failure observed",
    "deployment_activated": "Deployment change observed",
    "incident_recovered": "Incident resolved",
    "remediation_rollback_requested": "Rollback requested",
}
PUBLIC_REMEDIATION_LABELS = {
    "remediation_proposal_created": "Remediation proposed",
    "remediation_approved": "Human approved remediation",
    "remediation_rejected": "Human rejected remediation",
    "remediation_execution_started": "Remediation started",
    "remediation_execution_succeeded": "Remediation executed",
    "remediation_verification_started": "Recovery verification started",
    "remediation_verification_completed": "Recovery verified",
    "remediation_completed": "Remediation completed",
    "remediation_expired": "Remediation proposal expired",
    "remediation_execution_failed": "Remediation failed safely",
}


def public_error(
    request: Request,
    status_code: int,
    code: PublicErrorCode,
    message: str,
    *,
    retry_after: int | None = None,
) -> HTTPException:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code.value,
            "message": message,
            "request_id": request.state.request_id,
        },
        headers=headers,
    )


async def require_public_session(request: Request) -> UUID:
    raw = request.cookies.get(SESSION_COOKIE)
    try:
        session_id = UUID(raw) if raw else None
    except ValueError:
        session_id = None
    if session_id is None:
        raise public_error(
            request,
            401,
            PublicErrorCode.SESSION_EXPIRED,
            "Demo session is missing or expired",
        )
    try:
        await public_store.require_session(session_id)
    except DemoSessionExpiredError as exc:
        raise public_error(
            request,
            401,
            PublicErrorCode.SESSION_EXPIRED,
            "Demo session is missing or expired",
        ) from exc
    return session_id


async def enforce_public_limit(
    request: Request, session_id: UUID, action: str, limit: int
) -> None:
    try:
        await public_store.consume_rate_limit(
            str(session_id),
            action,
            limit,
            settings.public_rate_limit_window_seconds,
        )
    except PublicRateLimitError as exc:
        log_event(
            logger,
            "public_rate_limited",
            "Public demo request rejected",
            request_id=request.state.request_id,
            session_id=session_id,
            action_type=action,
        )
        raise public_error(
            request,
            429,
            PublicErrorCode.RATE_LIMITED,
            "Public demo rate limit exceeded",
            retry_after=exc.retry_after,
        ) from exc


def public_incident_model(incident: IncidentDetail) -> PublicIncident:
    activity = [
        PublicActivity(
            event=PUBLIC_EVENT_LABELS[event.event_type],
            occurred_at=event.occurred_at,
        )
        for event in incident.events
        if event.event_type in PUBLIC_EVENT_LABELS
    ][-30:]
    return PublicIncident(
        **incident.model_dump(exclude={"events", "description"}),
        activity=activity,
    )


def public_remediation_model(proposal: RemediationProposal) -> PublicRemediation:
    verification = proposal.verification_result
    return PublicRemediation(
        proposal_id=proposal.proposal_id,
        incident_id=proposal.incident_id,
        action_type=proposal.action_type,
        status=proposal.status,
        summary=proposal.summary,
        expires_at=proposal.expires_at,
        verification_result=(
            {key: bool(value) for key, value in verification.items()}
            if verification
            else None
        ),
        activity=[
            PublicActivity(
                event=PUBLIC_REMEDIATION_LABELS[event.event_type],
                occurred_at=event.occurred_at,
            )
            for event in proposal.audit_events
            if event.event_type in PUBLIC_REMEDIATION_LABELS
        ],
    )


def public_report_model(report: InvestigationReport) -> PublicInvestigationReport:
    cited = report.cited_evidence_ids()
    evidence = [
        PublicEvidence(
            evidence_id=item.evidence_id,
            source=item.source,
            evidence_type=item.evidence_type,
            timestamp=item.timestamp,
            summary=item.summary,
        )
        for item in report.evidence_catalog
        if item.evidence_id in cited
    ]
    return PublicInvestigationReport(
        executive_summary=report.executive_summary,
        timeline=report.timeline,
        primary_hypothesis=report.primary_hypothesis,
        alternative_hypotheses=report.alternative_hypotheses,
        evidence=evidence,
        recommended_actions=report.recommended_actions,
        uncertainties=report.uncertainties,
        activity=report.activity_trace,
        model=report.model,
        model_calls=report.metrics.model_calls,
        tool_calls=report.metrics.tool_calls,
        duration_ms=report.metrics.duration_ms,
    )


@app.post("/api/demo/incidents", response_model=PublicIncident, status_code=202)
async def public_create_incident(
    payload: PublicIncidentRequest, request: Request, response: Response
) -> PublicIncident:
    await public_store.cleanup()
    try:
        await public_store.consume_rate_limit(
            "global",
            "incident",
            settings.public_incident_limit,
            settings.public_rate_limit_window_seconds,
        )
    except PublicRateLimitError as exc:
        raise public_error(
            request,
            429,
            PublicErrorCode.RATE_LIMITED,
            "The public demo is temporarily at capacity",
            retry_after=exc.retry_after,
        ) from exc
    raw = request.cookies.get(SESSION_COOKIE)
    try:
        session_id = UUID(raw) if raw else await public_store.create_session()
        await public_store.require_session(session_id)
    except (ValueError, DemoSessionExpiredError):
        session_id = await public_store.create_session()
    try:
        incident = await incident_lab.start(
            payload.scenario,
            settings.incident_default_duration_seconds,
            session_id,
        )
    except ActiveIncidentError as exc:
        raise public_error(
            request,
            409,
            PublicErrorCode.CONFLICT,
            "The demo lab is currently running another incident",
        ) from exc
    response.set_cookie(
        SESSION_COOKIE,
        str(session_id),
        max_age=settings.demo_session_ttl_seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/api/demo",
    )
    return public_incident_model(await incident_lab.get_incident(incident.incident_id))


@app.get("/api/demo/incidents/{incident_id}", response_model=PublicIncident)
async def public_get_incident(
    incident_id: UUID,
    request: Request,
    session_id: Annotated[UUID, Depends(require_public_session)],
) -> PublicIncident:
    if not await public_store.owns_incident(session_id, incident_id):
        raise public_error(
            request, 404, PublicErrorCode.NOT_FOUND, "Incident not found"
        )
    return public_incident_model(await incident_lab.get_incident(incident_id))


@app.post(
    "/api/demo/incidents/{incident_id}/investigate",
    response_model=PublicInvestigation,
)
async def public_investigate(
    incident_id: UUID,
    request: Request,
    session_id: Annotated[UUID, Depends(require_public_session)],
) -> PublicInvestigation:
    if not await public_store.owns_incident(session_id, incident_id):
        raise public_error(
            request, 404, PublicErrorCode.NOT_FOUND, "Incident not found"
        )
    await enforce_public_limit(
        request, session_id, "investigation", settings.public_investigation_limit
    )
    try:
        report = await investigator.investigate(incident_id)
    except (
        InvestigationConfigurationError,
        InvestigationProviderError,
        InvestigationBudgetExceeded,
        InvalidInvestigationReport,
        DiagnosticToolFailure,
        TimeoutError,
    ) as exc:
        log_event(
            logger,
            "public_investigation_failed",
            "Public investigation failed safely",
            request_id=request.state.request_id,
            session_id=session_id,
            incident_id=incident_id,
            error_type=type(exc).__name__,
        )
        raise public_error(
            request,
            503,
            PublicErrorCode.TEMPORARILY_UNAVAILABLE,
            "Investigation is temporarily unavailable",
        ) from exc
    except InvestigationAlreadyRunningError as exc:
        raise public_error(
            request,
            409,
            PublicErrorCode.CONFLICT,
            "An investigation is already running",
        ) from exc
    return PublicInvestigation(
        investigation_id=report.investigation_id,
        incident_id=incident_id,
        status="completed",
        report=public_report_model(report),
    )


@app.get(
    "/api/demo/investigations/{investigation_id}",
    response_model=PublicInvestigation,
)
async def public_get_investigation(
    investigation_id: UUID,
    request: Request,
    session_id: Annotated[UUID, Depends(require_public_session)],
) -> PublicInvestigation:
    if not await public_store.owns_investigation(session_id, investigation_id):
        raise public_error(
            request, 404, PublicErrorCode.NOT_FOUND, "Investigation not found"
        )
    record = await investigation_store.get(investigation_id)
    if record is None:
        raise public_error(
            request, 404, PublicErrorCode.NOT_FOUND, "Investigation not found"
        )
    return PublicInvestigation(
        investigation_id=record.investigation_id,
        incident_id=record.incident_id,
        status=record.status,
        report=public_report_model(record.report) if record.report else None,
    )


@app.post(
    "/api/demo/investigations/{investigation_id}/remediation",
    response_model=PublicRemediation,
    status_code=201,
)
async def public_propose_remediation(
    investigation_id: UUID,
    request: Request,
    session_id: Annotated[UUID, Depends(require_public_session)],
) -> PublicRemediation:
    if not await public_store.owns_investigation(session_id, investigation_id):
        raise public_error(
            request, 404, PublicErrorCode.NOT_FOUND, "Investigation not found"
        )
    await enforce_public_limit(
        request, session_id, "remediation", settings.public_remediation_limit
    )
    try:
        proposal = await remediation_service.propose(investigation_id)
    except (ProposalNotFoundError, ProposalPolicyError) as exc:
        raise public_error(
            request,
            409,
            PublicErrorCode.CONFLICT,
            "Remediation proposal is unavailable",
        ) from exc
    return public_remediation_model(proposal)


@app.post(
    "/api/demo/remediations/{proposal_id}/approve",
    response_model=PublicRemediation,
)
async def public_approve_remediation(
    proposal_id: UUID,
    request: Request,
    session_id: Annotated[UUID, Depends(require_public_session)],
) -> PublicRemediation:
    if not await public_store.owns_proposal(session_id, proposal_id):
        raise public_error(
            request, 404, PublicErrorCode.NOT_FOUND, "Remediation not found"
        )
    await enforce_public_limit(
        request, session_id, "remediation", settings.public_remediation_limit
    )
    try:
        proposal = await remediation_service.approve(proposal_id)
    except (ProposalExpiredError, ProposalStateError) as exc:
        raise public_error(
            request,
            409,
            PublicErrorCode.CONFLICT,
            "Remediation cannot be approved",
        ) from exc
    return public_remediation_model(proposal)


@app.post(
    "/api/demo/remediations/{proposal_id}/execute",
    response_model=PublicRemediation,
)
async def public_execute_remediation(
    proposal_id: UUID,
    request: Request,
    session_id: Annotated[UUID, Depends(require_public_session)],
) -> PublicRemediation:
    if not await public_store.owns_proposal(session_id, proposal_id):
        raise public_error(
            request, 404, PublicErrorCode.NOT_FOUND, "Remediation not found"
        )
    await enforce_public_limit(
        request, session_id, "remediation", settings.public_remediation_limit
    )
    try:
        proposal = await remediation_service.execute(proposal_id)
    except ProposalExpiredError as exc:
        raise public_error(
            request,
            410,
            PublicErrorCode.CONFLICT,
            "Remediation proposal expired",
        ) from exc
    except (
        ProposalPolicyError,
        ProposalStateError,
        RemediationExecutionError,
    ) as exc:
        raise public_error(
            request,
            409,
            PublicErrorCode.CONFLICT,
            "Remediation cannot proceed",
        ) from exc
    return public_remediation_model(proposal)
