import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.config import get_settings
from app.database import DatabaseCheck, database, get_database_check
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

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
incident_lab = build_incident_lab(database, settings)


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
