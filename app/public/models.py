from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.evidence import EvidenceSource, EvidenceType
from app.investigation.models import (
    AlternativeHypothesis,
    PrimaryHypothesis,
    RecommendedAction,
    TimelineEntry,
    ToolActivity,
)
from app.models import IncidentStatus, ScenarioType
from app.remediation.models import RemediationAction, RemediationStatus


class PublicIncidentRequest(BaseModel):
    scenario: ScenarioType


class PublicActivity(BaseModel):
    event: str
    occurred_at: datetime


class PublicIncident(BaseModel):
    incident_id: UUID
    scenario: ScenarioType
    status: IncidentStatus
    started_at: datetime
    ended_at: datetime | None = None
    activity: list[PublicActivity] = Field(max_length=30)


class PublicInvestigation(BaseModel):
    investigation_id: UUID
    incident_id: UUID
    status: str
    report: "PublicInvestigationReport | None" = None


class PublicEvidence(BaseModel):
    evidence_id: str
    source: EvidenceSource
    evidence_type: EvidenceType
    timestamp: datetime | None = None
    summary: str


class PublicInvestigationReport(BaseModel):
    executive_summary: str
    timeline: list[TimelineEntry] = Field(max_length=20)
    primary_hypothesis: PrimaryHypothesis
    alternative_hypotheses: list[AlternativeHypothesis] = Field(max_length=5)
    evidence: list[PublicEvidence] = Field(max_length=100)
    recommended_actions: list[RecommendedAction] = Field(max_length=10)
    uncertainties: list[str] = Field(max_length=10)
    activity: list[ToolActivity] = Field(max_length=10)
    model: str
    model_calls: int
    tool_calls: int
    duration_ms: int


class PublicRemediation(BaseModel):
    proposal_id: UUID
    incident_id: UUID
    action_type: RemediationAction
    status: RemediationStatus
    summary: str
    expires_at: datetime
    verification_result: dict[str, bool] | None = None
    activity: list[PublicActivity] = Field(max_length=20)


class PublicErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    SESSION_EXPIRED = "session_expired"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


class PublicError(BaseModel):
    code: PublicErrorCode
    message: str
    request_id: str
