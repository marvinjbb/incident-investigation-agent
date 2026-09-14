from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, get_remediation_service
from app.remediation.models import (
    RemediationAction,
    RemediationProposal,
    RemediationStatus,
)
from app.remediation.service import ProposalStateError


def proposal(status=RemediationStatus.PENDING_APPROVAL):
    now = datetime.now(UTC)
    return RemediationProposal(
        proposal_id=uuid4(),
        incident_id=uuid4(),
        investigation_id=uuid4(),
        action_type=RemediationAction.TERMINATE_DEMO_BLOCKER,
        status=status,
        summary="Terminate the controlled blocker",
        reason="Validated blocking evidence",
        supporting_evidence_ids=["ev_1"],
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )


class Service:
    def __init__(self, item, error=None):
        self.item = item
        self.error = error

    async def propose(self, investigation_id):
        if self.error:
            raise self.error
        return self.item

    async def get(self, proposal_id):
        return self.item

    async def list_for_incident(self, incident_id):
        return [self.item]

    async def approve(self, proposal_id):
        if self.error:
            raise self.error
        return self.item.model_copy(update={"status": RemediationStatus.APPROVED})

    async def reject(self, proposal_id):
        return self.item.model_copy(update={"status": RemediationStatus.REJECTED})

    async def execute(self, proposal_id):
        return self.item.model_copy(update={"status": RemediationStatus.SUCCEEDED})


@pytest.mark.asyncio
async def test_remediation_lifecycle_routes_return_bounded_models():
    item = proposal()
    app.dependency_overrides[get_remediation_service] = lambda: Service(item)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                f"/demo/investigations/{item.investigation_id}/remediation-proposals"
            )
            listed = await client.get(
                f"/demo/incidents/{item.incident_id}/remediations"
            )
            approved = await client.post(
                f"/demo/remediations/{item.proposal_id}/approve"
            )
            rejected = await client.post(
                f"/demo/remediations/{item.proposal_id}/reject"
            )
            executed = await client.post(
                f"/demo/remediations/{item.proposal_id}/execute"
            )
            retrieved = await client.get(f"/demo/remediations/{item.proposal_id}")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert len(listed.json()) == 1
    assert approved.json()["status"] == "approved"
    assert rejected.json()["status"] == "rejected"
    assert executed.json()["status"] == "succeeded"
    assert retrieved.json()["proposal_id"] == str(item.proposal_id)


@pytest.mark.asyncio
async def test_remediation_api_returns_generic_state_error():
    item = proposal()
    app.dependency_overrides[get_remediation_service] = lambda: Service(
        item, ProposalStateError("internal state detail")
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/demo/remediations/{item.proposal_id}/approve"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {"detail": "Remediation cannot proceed"}
