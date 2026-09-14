from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.evidence import EvidenceItem, EvidenceSource, EvidenceType
from app.investigation.models import (
    InvestigationMetrics,
    InvestigationRecord,
    InvestigationReport,
    KeyEvidence,
    PrimaryHypothesis,
)
from app.investigation.provider import InvestigationConfigurationError
from app.lab import IncidentNotFoundError
from app.main import (
    app,
    get_incident_lab,
    get_investigation_store,
    get_investigator,
)


def report(incident_id):
    return InvestigationReport(
        investigation_id=uuid4(),
        incident_id=incident_id,
        incident_status="active",
        executive_summary="Grounded summary.",
        executive_summary_evidence_ids=["ev_1"],
        timeline=[],
        primary_hypothesis=PrimaryHypothesis(
            cause="Grounded cause.",
            confidence="high",
            evidence_ids=["ev_1"],
            explanation="Evidence supports it.",
        ),
        alternative_hypotheses=[],
        key_evidence=[KeyEvidence(evidence_id="ev_1", significance="Material")],
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
                summary="Incident metadata.",
            )
        ],
        metrics=InvestigationMetrics(model_calls=1, tool_calls=0, duration_ms=1),
    )


class Service:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def investigate(self, incident_id):
        if self.error:
            raise self.error
        return self.result


class Store:
    def __init__(self, record=None):
        self.record = record

    async def get(self, investigation_id):
        return self.record

    async def list_for_incident(self, incident_id):
        return [self.record] if self.record else []


class Lab:
    async def get_incident(self, incident_id):
        if incident_id.int == 0:
            raise IncidentNotFoundError("missing")
        return object()


@pytest.mark.asyncio
async def test_investigation_api_returns_report_and_persisted_record() -> None:
    incident_id = uuid4()
    result = report(incident_id)
    record = InvestigationRecord(
        investigation_id=result.investigation_id,
        incident_id=incident_id,
        status="completed",
        started_at=result.generated_at,
        completed_at=result.generated_at,
        model=result.model,
        report=result,
        tool_trace=[],
        error_type=None,
    )
    app.dependency_overrides[get_investigator] = lambda: Service(result)
    app.dependency_overrides[get_investigation_store] = lambda: Store(record)
    app.dependency_overrides[get_incident_lab] = lambda: Lab()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(f"/demo/incidents/{incident_id}/investigate")
            retrieved = await client.get(
                f"/demo/investigations/{result.investigation_id}"
            )
            listed = await client.get(f"/demo/incidents/{incident_id}/investigations")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200
    assert retrieved.json()["status"] == "completed"
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_investigation_api_maps_missing_configuration_safely() -> None:
    app.dependency_overrides[get_investigator] = lambda: Service(
        error=InvestigationConfigurationError("do not expose")
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/demo/incidents/{uuid4()}/investigate")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "AI investigation is not configured"}
