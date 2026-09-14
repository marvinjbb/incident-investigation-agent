from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.evidence import EvidenceItem, EvidenceSource, EvidenceType
from app.investigation.evaluation import evaluate_report
from app.investigation.models import (
    InvestigationMetrics,
    InvestigationReport,
    KeyEvidence,
    PrimaryHypothesis,
)
from app.models import ScenarioType


def make_evidence(incident_id, evidence_id, evidence_type):
    source = {
        EvidenceType.BLOCKED_SESSION: EvidenceSource.POSTGRESQL,
        EvidenceType.POOL_STATE: EvidenceSource.CONNECTION_POOL,
        EvidenceType.CONNECTION_UTILIZATION: EvidenceSource.POSTGRESQL,
        EvidenceType.DEPLOYMENT_CHANGE: EvidenceSource.DEPLOYMENT,
        EvidenceType.APPLICATION_ERROR: EvidenceSource.APPLICATION_LOG,
    }[evidence_type]
    return EvidenceItem(
        evidence_id=evidence_id,
        source=source,
        evidence_type=evidence_type,
        incident_id=incident_id,
        summary=evidence_type.value,
    )


@pytest.mark.parametrize(
    ("scenario", "cause", "types"),
    [
        (
            ScenarioType.BLOCKED_QUERY,
            "PostgreSQL lock contention blocked the query.",
            [EvidenceType.BLOCKED_SESSION],
        ),
        (
            ScenarioType.CONNECTION_EXHAUSTION,
            "The application pool was saturated; PostgreSQL retained capacity.",
            [EvidenceType.POOL_STATE, EvidenceType.CONNECTION_UTILIZATION],
        ),
        (
            ScenarioType.BAD_DEPLOYMENT,
            "v2-bad caused a schema mismatch and UndefinedColumn error.",
            [EvidenceType.DEPLOYMENT_CHANGE, EvidenceType.APPLICATION_ERROR],
        ),
    ],
)
def test_deterministic_scenario_evaluations(scenario, cause, types) -> None:
    incident_id = uuid4()
    items = {
        f"ev_{index}": make_evidence(incident_id, f"ev_{index}", evidence_type)
        for index, evidence_type in enumerate(types)
    }
    evidence_ids = list(items)
    report = InvestigationReport(
        investigation_id=uuid4(),
        incident_id=incident_id,
        incident_status="active",
        executive_summary=cause,
        executive_summary_evidence_ids=evidence_ids,
        timeline=[],
        primary_hypothesis=PrimaryHypothesis(
            cause=cause,
            confidence="high",
            evidence_ids=evidence_ids,
            explanation=cause,
        ),
        alternative_hypotheses=[],
        key_evidence=[
            KeyEvidence(evidence_id=item, significance="material")
            for item in evidence_ids
        ],
        recommended_actions=[],
        uncertainties=[],
        runbook_references=[],
        generated_at=datetime.now(UTC),
        model="test",
        activity_trace=[],
        evidence_catalog=list(items.values()),
        metrics=InvestigationMetrics(model_calls=2, tool_calls=2, duration_ms=10),
    )

    result = evaluate_report(scenario, report, items, 6, 10)

    assert result.passed, result.checks
