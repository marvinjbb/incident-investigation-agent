import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EvidenceSource(StrEnum):
    INCIDENT = "incident"
    INCIDENT_EVENT = "incident_event"
    APPLICATION_LOG = "application_log"
    POSTGRESQL = "postgresql"
    CONNECTION_POOL = "connection_pool"
    DEPLOYMENT = "deployment"
    RUNBOOK = "runbook"


class EvidenceType(StrEnum):
    INCIDENT_METADATA = "incident_metadata"
    INCIDENT_EVENT = "incident_event"
    BLOCKED_SESSION = "blocked_session"
    CONNECTION_UTILIZATION = "connection_utilization"
    POOL_STATE = "pool_state"
    APPLICATION_ERROR = "application_error"
    APPLICATION_LOG = "application_log"
    DEPLOYMENT_CHANGE = "deployment_change"
    TROUBLESHOOTING_GUIDANCE = "troubleshooting_guidance"


class EvidenceItem(BaseModel):
    evidence_id: str
    source: EvidenceSource
    evidence_type: EvidenceType
    timestamp: datetime | None = None
    incident_id: UUID | None = None
    summary: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)
    reference: str | None = Field(default=None, max_length=300)


class EvidenceBundle(BaseModel):
    incident_id: UUID
    scenario: str
    collected_at: datetime
    items: list[EvidenceItem] = Field(max_length=100)


def make_evidence_id(
    source: EvidenceSource, evidence_type: EvidenceType, reference: str, details: object
) -> str:
    canonical = json.dumps(details, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{source.value}:{evidence_type.value}:{reference}:{canonical}".encode()
    ).hexdigest()[:20]
    return f"ev_{digest}"
