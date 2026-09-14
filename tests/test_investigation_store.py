from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.evidence import EvidenceItem, EvidenceSource, EvidenceType
from app.investigation.models import (
    InvestigationMetrics,
    InvestigationReport,
    KeyEvidence,
    PrimaryHypothesis,
)
from app.investigation.store import InvestigationStore


class Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return [self.row] if self.row else []


class Connection:
    def __init__(self):
        self.calls = []
        self.row = None

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return Cursor(self.row)

    async def commit(self):
        return None


class Database:
    def __init__(self):
        self.connection = Connection()

    @asynccontextmanager
    async def control_connection(self):
        yield self.connection


def make_report(incident_id):
    return InvestigationReport(
        investigation_id=uuid4(),
        incident_id=incident_id,
        incident_status="resolved",
        executive_summary="summary",
        executive_summary_evidence_ids=["ev_1"],
        timeline=[],
        primary_hypothesis=PrimaryHypothesis(
            cause="cause",
            confidence="high",
            evidence_ids=["ev_1"],
            explanation="explanation",
        ),
        alternative_hypotheses=[],
        key_evidence=[KeyEvidence(evidence_id="ev_1", significance="significant")],
        recommended_actions=[],
        uncertainties=[],
        runbook_references=[],
        generated_at=datetime.now(UTC),
        model="test-model",
        activity_trace=[],
        evidence_catalog=[
            EvidenceItem(
                evidence_id="ev_1",
                source=EvidenceSource.INCIDENT,
                evidence_type=EvidenceType.INCIDENT_METADATA,
                incident_id=incident_id,
                summary="metadata",
            )
        ],
        metrics=InvestigationMetrics(model_calls=2, tool_calls=1, duration_ms=2),
    )


@pytest.mark.asyncio
async def test_investigation_store_persists_safe_report_and_trace() -> None:
    database = Database()
    store = InvestigationStore(database)  # type: ignore[arg-type]
    incident_id = uuid4()
    report = make_report(incident_id)

    await store.start(report.investigation_id, incident_id, report.model)
    await store.complete(report.investigation_id, report)

    assert "INSERT INTO investigations" in database.connection.calls[0][0]
    assert "UPDATE investigations" in database.connection.calls[1][0]
    persisted = database.connection.calls[1][1]
    assert "executive_summary" in persisted[1].obj
    assert "reasoning" not in persisted[1].obj
