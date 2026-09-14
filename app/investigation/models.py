from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evidence import EvidenceItem


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TimelineEntry(StrictModel):
    timestamp: datetime
    event: str = Field(min_length=1, max_length=300)
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Exact application-owned evidence_id values; never evidence text.",
    )


class PrimaryHypothesis(StrictModel):
    cause: str = Field(min_length=1, max_length=500)
    confidence: Confidence
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=10,
        description="Exact application-owned evidence_id values; never evidence text.",
    )
    explanation: str = Field(min_length=1, max_length=1200)


class AlternativeHypothesis(StrictModel):
    cause: str = Field(min_length=1, max_length=500)
    evidence_for: list[str] = Field(
        max_length=5,
        description=(
            "Exact application-owned evidence_id values supporting this hypothesis."
        ),
    )
    evidence_against: list[str] = Field(
        max_length=5,
        description=(
            "Exact application-owned evidence_id values contradicting this hypothesis."
        ),
    )


class KeyEvidence(StrictModel):
    evidence_id: str = Field(
        description=(
            "One exact application-owned evidence_id value; never evidence text."
        )
    )
    significance: str = Field(min_length=1, max_length=500)


class RecommendedAction(StrictModel):
    action: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    approval_required: bool
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Exact application-owned evidence_id values; never evidence text.",
    )


class ReportDraft(StrictModel):
    executive_summary: str = Field(min_length=1, max_length=1200)
    executive_summary_evidence_ids: list[str] = Field(
        min_length=1,
        max_length=10,
        description=(
            "Exact application-owned evidence_id values; never summaries or prose."
        ),
    )
    timeline: list[TimelineEntry] = Field(max_length=20)
    primary_hypothesis: PrimaryHypothesis
    alternative_hypotheses: list[AlternativeHypothesis] = Field(max_length=5)
    key_evidence: list[KeyEvidence] = Field(min_length=1, max_length=15)
    recommended_actions: list[RecommendedAction] = Field(max_length=10)
    uncertainties: list[str] = Field(max_length=10)
    runbook_references: list[str] = Field(max_length=3)

    def cited_evidence_ids(self) -> set[str]:
        cited = set(self.executive_summary_evidence_ids)
        for entry in self.timeline:
            cited.update(entry.evidence_ids)
        cited.update(self.primary_hypothesis.evidence_ids)
        for hypothesis in self.alternative_hypotheses:
            cited.update(hypothesis.evidence_for)
            cited.update(hypothesis.evidence_against)
        cited.update(item.evidence_id for item in self.key_evidence)
        for action in self.recommended_actions:
            cited.update(action.evidence_ids)
        return cited


class ToolActivity(StrictModel):
    tool: str
    status: str
    timestamp: datetime
    result_count: int = Field(ge=0)


class InvestigationMetrics(StrictModel):
    model_calls: int = Field(ge=1)
    tool_calls: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class InvestigationReport(ReportDraft):
    investigation_id: UUID
    incident_id: UUID
    incident_status: str
    generated_at: datetime
    model: str
    activity_trace: list[ToolActivity] = Field(max_length=10)
    evidence_catalog: list[EvidenceItem] = Field(min_length=1, max_length=100)
    metrics: InvestigationMetrics

    @model_validator(mode="after")
    def actions_never_claim_execution(self) -> "InvestigationReport":
        forbidden = ("executed", "terminated", "rolled back", "applied")
        for action in self.recommended_actions:
            if any(word in action.action.lower() for word in forbidden):
                raise ValueError("recommended actions cannot claim execution")
        known = {item.evidence_id for item in self.evidence_catalog}
        if not self.cited_evidence_ids().issubset(known):
            raise ValueError("report citations must exist in the evidence catalog")
        return self


class InvestigationRecord(StrictModel):
    investigation_id: UUID
    incident_id: UUID
    status: str
    started_at: datetime
    completed_at: datetime | None
    model: str
    report: InvestigationReport | None
    tool_trace: list[ToolActivity]
    error_type: str | None
