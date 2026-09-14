import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

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
from app.investigation.store import InvestigationStore
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


class HealthResponse(BaseModel):
    status: str


class DatabaseHealthResponse(BaseModel):
    status: str
    database: str


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await database.open()
    await database.initialize_schema()
    await database.recover_stale_state()
    try:
        yield
    finally:
        await incident_lab.shutdown()
        await database.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


def get_incident_lab() -> IncidentLab:
    return incident_lab


def get_evidence_collector() -> IncidentEvidenceCollector:
    return evidence_collector


def get_investigator() -> IncidentInvestigator:
    return investigator


def get_investigation_store() -> InvestigationStore:
    return investigation_store


@app.middleware("http")
async def log_request(request: Request, call_next) -> Response:
    response = await call_next(request)
    log_event(
        logger,
        "request_completed",
        "HTTP request completed",
        path=request.url.path,
    )
    return response


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
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
