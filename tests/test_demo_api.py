from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.lab import ActiveIncidentError, WorkloadUnavailableError
from app.main import app, get_incident_lab
from app.models import (
    Deployment,
    Incident,
    IncidentDetail,
    IncidentStatus,
    PoolState,
    ScenarioType,
    WorkloadResponse,
)


class FakeLab:
    def __init__(self) -> None:
        self.started: list[tuple[ScenarioType, int]] = []
        self.reject_start = False
        self.workload_fails = False
        self.incident_id = uuid4()

    async def start(self, scenario: ScenarioType, duration: int) -> Incident:
        if self.reject_start:
            raise ActiveIncidentError("A demo incident is already active")
        self.started.append((scenario, duration))
        return Incident(
            incident_id=self.incident_id,
            scenario=scenario,
            status=IncidentStatus.STARTING,
            started_at=datetime.now(UTC),
            description="bounded demo",
        )

    async def get_incident(self, incident_id: UUID) -> IncidentDetail:
        return IncidentDetail(
            incident_id=incident_id,
            scenario=ScenarioType.BLOCKED_QUERY,
            status=IncidentStatus.ACTIVE,
            started_at=datetime.now(UTC),
            description="bounded demo",
            events=[],
        )

    async def recover(self, incident_id: UUID) -> IncidentDetail:
        return (await self.get_incident(incident_id)).model_copy(
            update={"status": IncidentStatus.RESOLVED, "ended_at": datetime.now(UTC)}
        )

    async def workload(self, _: str) -> WorkloadResponse:
        if self.workload_fails:
            raise WorkloadUnavailableError
        return WorkloadResponse(status="ok", version="v1", message="workload completed")

    async def deployments(self) -> list[Deployment]:
        return [
            Deployment(
                deployment_id=uuid4(),
                version="v1",
                deployed_at=datetime.now(UTC),
                status="healthy",
                became_active=True,
            )
        ]

    def pool_state(self) -> PoolState:
        return PoolState(size=3, available=3, waiting=0, maximum=3)


@pytest.fixture
async def demo_client() -> tuple[AsyncClient, FakeLab]:
    fake_lab = FakeLab()
    app.dependency_overrides[get_incident_lab] = lambda: fake_lab
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, fake_lab
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "scenario"),
    [
        ("blocked-query", ScenarioType.BLOCKED_QUERY),
        ("connection-exhaustion", ScenarioType.CONNECTION_EXHAUSTION),
        ("bad-deployment", ScenarioType.BAD_DEPLOYMENT),
    ],
)
async def test_allowlisted_incident_routes(
    demo_client: tuple[AsyncClient, FakeLab], route: str, scenario: ScenarioType
) -> None:
    client, fake_lab = demo_client

    response = await client.post(
        f"/demo/incidents/{route}", json={"duration_seconds": 5}
    )

    assert response.status_code == 202
    assert response.json()["scenario"] == scenario.value
    assert fake_lab.started == [(scenario, 5)]


@pytest.mark.asyncio
async def test_unknown_scenario_is_not_routable(
    demo_client: tuple[AsyncClient, FakeLab],
) -> None:
    client, _ = demo_client

    response = await client.post("/demo/incidents/arbitrary", json={})

    assert response.status_code == 405


@pytest.mark.asyncio
async def test_duration_is_bounded(demo_client: tuple[AsyncClient, FakeLab]) -> None:
    client, _ = demo_client

    too_short = await client.post(
        "/demo/incidents/blocked-query", json={"duration_seconds": 1}
    )
    too_long = await client.post(
        "/demo/incidents/blocked-query", json={"duration_seconds": 121}
    )

    assert too_short.status_code == 422
    assert too_long.status_code == 422


@pytest.mark.asyncio
async def test_only_one_active_incident(
    demo_client: tuple[AsyncClient, FakeLab],
) -> None:
    client, fake_lab = demo_client
    fake_lab.reject_start = True

    response = await client.post("/demo/incidents/blocked-query", json={})

    assert response.status_code == 409
    assert response.json() == {"detail": "A demo incident is already active"}


@pytest.mark.asyncio
async def test_incident_detail_and_recovery_are_structured(
    demo_client: tuple[AsyncClient, FakeLab],
) -> None:
    client, fake_lab = demo_client

    detail = await client.get(f"/demo/incidents/{fake_lab.incident_id}")
    recovery = await client.post(f"/demo/incidents/{fake_lab.incident_id}/recover")

    assert detail.status_code == 200
    assert detail.json()["status"] == "active"
    assert recovery.status_code == 200
    assert recovery.json()["status"] == "resolved"


@pytest.mark.asyncio
async def test_workload_and_diagnostics_responses(
    demo_client: tuple[AsyncClient, FakeLab],
) -> None:
    client, fake_lab = demo_client

    workload = await client.get("/demo/workload")
    deployments = await client.get("/demo/deployments")
    pool = await client.get("/demo/diagnostics/pool")
    fake_lab.workload_fails = True
    failed_workload = await client.get("/demo/workload")

    assert workload.json() == {
        "status": "ok",
        "version": "v1",
        "message": "workload completed",
    }
    assert deployments.status_code == 200
    assert deployments.json()[0]["version"] == "v1"
    assert pool.json() == {"size": 3, "available": 3, "waiting": 0, "maximum": 3}
    assert failed_workload.status_code == 503
    assert failed_workload.json() == {
        "detail": "Demo workload is temporarily unavailable"
    }
