from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemediationAction(StrEnum):
    TERMINATE_DEMO_BLOCKER = "terminate_demo_blocker"
    RELEASE_DEMO_POOL_PRESSURE = "release_demo_pool_pressure"
    ROLLBACK_DEMO_DEPLOYMENT = "rollback_demo_deployment"


class RemediationStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RemediationAuditEvent(StrictModel):
    audit_event_id: int
    proposal_id: UUID
    event_type: str
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class RemediationProposal(StrictModel):
    proposal_id: UUID
    incident_id: UUID
    investigation_id: UUID
    action_type: RemediationAction
    status: RemediationStatus
    summary: str
    reason: str
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=10)
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    completed_at: datetime | None = None
    execution_result: dict[str, Any] | None = None
    verification_result: dict[str, Any] | None = None
    audit_events: list[RemediationAuditEvent] = Field(
        default_factory=list, max_length=20
    )
