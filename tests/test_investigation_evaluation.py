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
    ToolActivity,
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
    scenario_tools = {
        ScenarioType.BLOCKED_QUERY: ["get_incident", "get_database_blocking"],
        ScenarioType.CONNECTION_EXHAUSTION: [
            "get_incident",
            "get_database_connections",
            "get_application_pool_state",
        ],
        ScenarioType.BAD_DEPLOYMENT: ["get_incident", "get_recent_deployments"],
    }
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
        activity_trace=[
            ToolActivity(
                tool=tool,
                status="completed",
                timestamp=datetime.now(UTC),
                result_count=1,
            )
            for tool in scenario_tools[scenario]
        ],
        evidence_catalog=list(items.values()),
        metrics=InvestigationMetrics(model_calls=2, tool_calls=2, duration_ms=10),
    )

    result = evaluate_report(scenario, report, items, 6, 10)

    assert result.passed, result.checks


def test_evaluation_rejects_pathological_call_everything_behavior() -> None:
    incident_id = uuid4()
    item = make_evidence(incident_id, "ev_blocked", EvidenceType.BLOCKED_SESSION)
    from app.investigation.evaluation import ALL_DIAGNOSTIC_TOOLS

    report = InvestigationReport(
        investigation_id=uuid4(),
        incident_id=incident_id,
        incident_status="active",
        executive_summary="PostgreSQL lock contention blocked the query.",
        executive_summary_evidence_ids=[item.evidence_id],
        timeline=[],
        primary_hypothesis=PrimaryHypothesis(
            cause="PostgreSQL lock contention blocked the query.",
            confidence="high",
            evidence_ids=[item.evidence_id],
            explanation="A blocking relationship was observed.",
        ),
        alternative_hypotheses=[],
        key_evidence=[
            KeyEvidence(evidence_id=item.evidence_id, significance="material")
        ],
        recommended_actions=[],
        uncertainties=[],
        runbook_references=[],
        generated_at=datetime.now(UTC),
        model="test",
        activity_trace=[
            ToolActivity(
                tool=tool,
                status="completed",
                timestamp=datetime.now(UTC),
                result_count=1,
            )
            for tool in ALL_DIAGNOSTIC_TOOLS
        ],
        evidence_catalog=[item],
        metrics=InvestigationMetrics(model_calls=2, tool_calls=8, duration_ms=10),
    )

    result = evaluate_report(
        ScenarioType.BLOCKED_QUERY,
        report,
        {item.evidence_id: item},
        6,
        10,
    )

    assert result.checks["selective_tool_use"] is False
    assert result.passed is False


def test_pool_evaluation_rejects_unmotivated_cross_domain_sweep() -> None:
    incident_id = uuid4()
    items = {
        "ev_pool": make_evidence(incident_id, "ev_pool", EvidenceType.POOL_STATE),
        "ev_capacity": make_evidence(
            incident_id, "ev_capacity", EvidenceType.CONNECTION_UTILIZATION
        ),
    }
    report = InvestigationReport(
        investigation_id=uuid4(),
        incident_id=incident_id,
        incident_status="active",
        executive_summary="The application pool was saturated.",
        executive_summary_evidence_ids=list(items),
        timeline=[],
        primary_hypothesis=PrimaryHypothesis(
            cause="The application pool was saturated.",
            confidence="high",
            evidence_ids=list(items),
            explanation="PostgreSQL retained capacity.",
        ),
        alternative_hypotheses=[],
        key_evidence=[
            KeyEvidence(evidence_id=item, significance="material") for item in items
        ],
        recommended_actions=[],
        uncertainties=[],
        runbook_references=[],
        generated_at=datetime.now(UTC),
        model="test",
        activity_trace=[
            ToolActivity(
                tool=tool,
                status="completed",
                timestamp=datetime.now(UTC),
                result_count=1,
            )
            for tool in (
                "get_incident",
                "get_incident_events",
                "get_application_pool_state",
                "get_database_connections",
                "get_database_blocking",
                "get_recent_deployments",
            )
        ],
        evidence_catalog=list(items.values()),
        metrics=InvestigationMetrics(model_calls=3, tool_calls=6, duration_ms=10),
    )

    result = evaluate_report(ScenarioType.CONNECTION_EXHAUSTION, report, items, 6, 10)

    assert result.checks["avoids_unmotivated_blocking_or_deployment"] is False
    assert result.passed is False
