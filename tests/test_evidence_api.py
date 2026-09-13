from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.evidence import EvidenceBundle, EvidenceItem, EvidenceSource, EvidenceType
from app.lab import IncidentNotFoundError
from app.main import app, get_evidence_collector


class FakeCollector:
    def __init__(self) -> None:
        self.missing = False

    async def collect(self, incident_id):
        if self.missing:
            raise IncidentNotFoundError("internal detail")
        return EvidenceBundle(
            incident_id=incident_id,
            scenario="blocked_query",
            collected_at=datetime.now(UTC),
            items=[
                EvidenceItem(
                    evidence_id="ev_test",
                    source=EvidenceSource.INCIDENT,
                    evidence_type=EvidenceType.INCIDENT_METADATA,
                    incident_id=incident_id,
                    summary="Incident is active.",
                    details={"status": "active"},
                    reference=f"incident:{incident_id}",
                )
            ],
        )


def test_evidence_bundle_has_a_hard_payload_bound() -> None:
    incident_id = uuid4()
    item = EvidenceItem(
        evidence_id="ev_test",
        source=EvidenceSource.INCIDENT,
        evidence_type=EvidenceType.INCIDENT_METADATA,
        incident_id=incident_id,
        summary="Bounded evidence.",
    )

    with pytest.raises(ValueError):
        EvidenceBundle(
            incident_id=incident_id,
            scenario="blocked_query",
            collected_at=datetime.now(UTC),
            items=[item] * 101,
        )


@pytest.mark.asyncio
async def test_evidence_endpoint_returns_structured_bundle() -> None:
    collector = FakeCollector()
    app.dependency_overrides[get_evidence_collector] = lambda: collector
    incident_id = uuid4()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/demo/incidents/{incident_id}/evidence")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["incident_id"] == str(incident_id)
    assert response.json()["items"][0]["source"] == "incident"


@pytest.mark.asyncio
async def test_evidence_endpoint_hides_internal_missing_detail() -> None:
    collector = FakeCollector()
    collector.missing = True
    app.dependency_overrides[get_evidence_collector] = lambda: collector
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/demo/incidents/{uuid4()}/evidence")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident not found"}
