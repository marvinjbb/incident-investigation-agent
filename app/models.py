from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ScenarioType(StrEnum):
    BLOCKED_QUERY = "blocked_query"
    CONNECTION_EXHAUSTION = "connection_exhaustion"
    BAD_DEPLOYMENT = "bad_deployment"


class IncidentStatus(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    RECOVERING = "recovering"
    RESOLVED = "resolved"
    FAILED = "failed"


class IncidentStartRequest(BaseModel):
    duration_seconds: int | None = Field(default=None, ge=3)


class IncidentEvent(BaseModel):
    event_id: int
    incident_id: UUID
    event_type: str
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class Incident(BaseModel):
    incident_id: UUID
    scenario: ScenarioType
    status: IncidentStatus
    started_at: datetime
    ended_at: datetime | None = None
    description: str


class IncidentDetail(Incident):
    events: list[IncidentEvent] = Field(default_factory=list)


class Deployment(BaseModel):
    deployment_id: UUID
    version: str
    deployed_at: datetime
    status: str
    became_active: bool


class WorkloadResponse(BaseModel):
    status: str
    version: str
    message: str


class PoolState(BaseModel):
    size: int
    available: int
    waiting: int
    maximum: int
