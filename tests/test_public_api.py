from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main
from app.models import Incident, IncidentDetail, IncidentStatus, ScenarioType
from app.public.store import DemoSessionExpiredError, PublicRateLimitError
from tests.test_investigation_api import report
from tests.test_remediation_api import proposal


class FakePublicStore:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.owned: set[UUID] = set()
        self.owned_investigations: set[UUID] = set()
        self.owned_proposals: set[UUID] = set()
        self.rate_limited = False

    async def create_session(self) -> UUID:
        return self.session_id

    async def cleanup(self) -> None:
        return None

    async def require_session(self, session_id: UUID) -> None:
        if session_id != self.session_id:
            raise DemoSessionExpiredError

    async def consume_rate_limit(self, *_: object) -> None:
        if self.rate_limited:
            raise PublicRateLimitError(30)

    async def owns_incident(self, session_id: UUID, incident_id: UUID) -> bool:
        return session_id == self.session_id and incident_id in self.owned

    async def owns_investigation(
        self, session_id: UUID, investigation_id: UUID
    ) -> bool:
        return (
            session_id == self.session_id
            and investigation_id in self.owned_investigations
        )

    async def owns_proposal(self, session_id: UUID, proposal_id: UUID) -> bool:
        return session_id == self.session_id and proposal_id in self.owned_proposals


class FakePublicLab:
    def __init__(self, store: FakePublicStore) -> None:
        self.store = store

    async def start(self, scenario: ScenarioType, _: int, session_id: UUID) -> Incident:
        assert session_id == self.store.session_id
        incident_id = uuid4()
        self.store.owned.add(incident_id)
        return Incident(
            incident_id=incident_id,
            scenario=scenario,
            status=IncidentStatus.STARTING,
            started_at=datetime.now(UTC),
            description="synthetic demo",
        )

    async def get_incident(self, incident_id: UUID) -> IncidentDetail:
        return IncidentDetail(
            incident_id=incident_id,
            scenario=ScenarioType.BLOCKED_QUERY,
            status=IncidentStatus.ACTIVE,
            started_at=datetime.now(UTC),
            description="synthetic demo",
            events=[],
        )


@pytest.fixture
async def public_client(monkeypatch: pytest.MonkeyPatch):
    store = FakePublicStore()
    monkeypatch.setattr(main, "public_store", store)
    monkeypatch.setattr(main, "incident_lab", FakePublicLab(store))
    async with AsyncClient(
        transport=ASGITransport(app=main.app), base_url="http://testserver"
    ) as client:
        yield client, store


@pytest.mark.asyncio
async def test_public_session_owns_created_incident(public_client) -> None:
    client, _ = public_client
    created = await client.post(
        "/api/demo/incidents", json={"scenario": "blocked_query"}
    )
    incident_id = created.json()["incident_id"]
    fetched = await client.get(f"/api/demo/incidents/{incident_id}")

    assert created.status_code == 202
    assert main.SESSION_COOKIE in created.cookies
    assert fetched.status_code == 200
    assert "description" not in fetched.json()


@pytest.mark.asyncio
async def test_cross_session_resource_is_hidden(public_client) -> None:
    client, _ = public_client
    client.cookies.set(main.SESSION_COOKIE, str(uuid4()), path="/api/demo")
    response = await client.get(f"/api/demo/incidents/{uuid4()}")

    assert response.status_code in {401, 404}
    assert "database" not in response.text.lower()


@pytest.mark.asyncio
async def test_public_rate_limit_is_safe(public_client) -> None:
    client, store = public_client
    store.rate_limited = True
    response = await client.post(
        "/api/demo/incidents", json={"scenario": "blocked_query"}
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert "The public demo is temporarily at capacity" in response.text


@pytest.mark.asyncio
async def test_production_origin_is_not_wildcard() -> None:
    assert "*" not in main.settings.allowed_origins


@pytest.mark.asyncio
async def test_cors_and_security_headers_are_restricted(public_client) -> None:
    client, _ = public_client
    allowed = await client.options(
        "/api/demo/incidents",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    denied = await client.options(
        "/api/demo/incidents",
        headers={
            "Origin": "https://attacker.invalid",
            "Access-Control-Request-Method": "POST",
        },
    )
    response = await client.get(
        f"/api/demo/incidents/{uuid4()}", headers={"X-Request-ID": "not-a-uuid"}
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers
    UUID(response.headers["x-request-id"])
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_public_report_omits_internal_evidence_details() -> None:
    public = main.public_report_model(report(uuid4())).model_dump(mode="json")

    assert public["evidence"]
    assert "details" not in public["evidence"][0]
    assert "reference" not in public["evidence"][0]
    assert "evidence_catalog" not in public


@pytest.mark.asyncio
async def test_public_remediation_is_owned_and_action_is_server_selected(
    public_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = public_client
    item = proposal()
    store.owned_investigations.add(item.investigation_id)
    store.owned_proposals.add(item.proposal_id)
    client.cookies.set(main.SESSION_COOKIE, str(store.session_id), path="/api/demo")

    class Service:
        async def propose(self, investigation_id: UUID):
            assert investigation_id == item.investigation_id
            return item

        async def approve(self, proposal_id: UUID):
            assert proposal_id == item.proposal_id
            return item.model_copy(update={"status": "approved"})

        async def execute(self, proposal_id: UUID):
            assert proposal_id == item.proposal_id
            return item.model_copy(update={"status": "succeeded"})

    monkeypatch.setattr(main, "remediation_service", Service())
    proposed = await client.post(
        f"/api/demo/investigations/{item.investigation_id}/remediation"
    )
    approved = await client.post(f"/api/demo/remediations/{item.proposal_id}/approve")
    executed = await client.post(f"/api/demo/remediations/{item.proposal_id}/execute")

    assert proposed.status_code == 201
    assert proposed.json()["action_type"] == item.action_type.value
    assert approved.json()["status"] == "approved"
    assert executed.json()["status"] == "succeeded"
    assert "reason" not in executed.json()
    assert "supporting_evidence_ids" not in executed.json()
