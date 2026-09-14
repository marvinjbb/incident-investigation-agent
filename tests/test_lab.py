import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from psycopg import errors

from app.config import Settings
from app.lab import ActiveIncidentError, IncidentLab, RemediationPreconditionError
from app.models import (
    Deployment,
    Incident,
    IncidentDetail,
    IncidentEvent,
    IncidentStatus,
    ScenarioType,
)


class FakeDatabase:
    def pool_state(self) -> dict[str, int]:
        return {"size": 3, "available": 2, "waiting": 0, "maximum": 3}


class FailingWorkloadConnection:
    async def execute(self, _: str):
        raise errors.UndefinedColumn('column "removed_message" does not exist')


class FailingWorkloadPool:
    @asynccontextmanager
    async def connection(self):
        yield FailingWorkloadConnection()


class FailingWorkloadDatabase(FakeDatabase):
    def __init__(self) -> None:
        self.pool = FailingWorkloadPool()


class FakeStore:
    def __init__(self) -> None:
        self.incidents: dict[UUID, Incident] = {}
        self.events: dict[UUID, list[IncidentEvent]] = {}
        self.event_number = 0
        self.resolved = asyncio.Event()

    async def create_incident(
        self, incident_id: UUID, scenario: ScenarioType, description: str
    ) -> Incident:
        incident = Incident(
            incident_id=incident_id,
            scenario=scenario,
            status=IncidentStatus.STARTING,
            started_at=datetime.now(UTC),
            description=description,
        )
        self.incidents[incident_id] = incident
        self.events[incident_id] = []
        return incident

    async def update_status(self, incident_id: UUID, status: IncidentStatus) -> None:
        current = self.incidents[incident_id]
        self.incidents[incident_id] = current.model_copy(
            update={
                "status": status,
                "ended_at": (
                    datetime.now(UTC)
                    if status in {IncidentStatus.RESOLVED, IncidentStatus.FAILED}
                    else None
                ),
            }
        )
        if status is IncidentStatus.RESOLVED:
            self.resolved.set()

    async def add_event(
        self,
        incident_id: UUID,
        event_type: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.event_number += 1
        self.events[incident_id].append(
            IncidentEvent(
                event_id=self.event_number,
                incident_id=incident_id,
                event_type=event_type,
                occurred_at=datetime.now(UTC),
                details=details or {},
            )
        )

    async def get_incident(self, incident_id: UUID) -> IncidentDetail | None:
        incident = self.incidents.get(incident_id)
        if incident is None:
            return None
        return IncidentDetail(**incident.model_dump(), events=self.events[incident_id])

    async def activate_deployment(
        self, version: str, status: str, became_active: bool = True
    ) -> Deployment:
        raise AssertionError("not used by this test")

    async def active_version(self) -> str:
        return "v1"

    async def list_deployments(self) -> list[Deployment]:
        return []


class BadDeploymentStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.active_deployment = "v1"
        self.deployments: list[Deployment] = []
        self.failure_observed = asyncio.Event()

    async def add_event(
        self,
        incident_id: UUID,
        event_type: str,
        details: dict[str, object] | None = None,
    ) -> None:
        await super().add_event(incident_id, event_type, details)
        if event_type == "request_failed":
            self.failure_observed.set()

    async def activate_deployment(
        self, version: str, status: str, became_active: bool = True
    ) -> Deployment:
        deployment = Deployment(
            deployment_id=uuid4(),
            version=version,
            deployed_at=datetime.now(UTC),
            status=status,
            became_active=became_active,
        )
        self.deployments.append(deployment)
        if became_active:
            self.active_deployment = version
        return deployment

    async def active_version(self) -> str:
        return self.active_deployment

    async def list_deployments(self) -> list[Deployment]:
        return list(reversed(self.deployments))


@pytest.mark.asyncio
async def test_incident_transitions_and_manual_recovery() -> None:
    store = FakeStore()
    scenario_started = asyncio.Event()

    async def controlled_scenario(_: UUID, stop_event: asyncio.Event, __: int) -> None:
        scenario_started.set()
        await stop_event.wait()

    lab = IncidentLab(
        FakeDatabase(),  # type: ignore[arg-type]
        store,
        Settings(),
        {ScenarioType.BLOCKED_QUERY: controlled_scenario},
    )
    incident = await lab.start(ScenarioType.BLOCKED_QUERY, 10)
    await scenario_started.wait()

    with pytest.raises(ActiveIncidentError):
        await lab.start(ScenarioType.BLOCKED_QUERY, 10)

    recovered = await lab.recover(incident.incident_id)

    assert recovered.status is IncidentStatus.RESOLVED
    assert recovered.ended_at is not None
    assert [event.event_type for event in recovered.events] == [
        "incident_started",
        "recovery_requested",
        "incident_recovered",
    ]


@pytest.mark.asyncio
async def test_incident_auto_cleans_up_when_handler_finishes() -> None:
    store = FakeStore()

    async def completed_scenario(_: UUID, __: asyncio.Event, ___: int) -> None:
        return None

    lab = IncidentLab(
        FakeDatabase(),  # type: ignore[arg-type]
        store,
        Settings(),
        {ScenarioType.BAD_DEPLOYMENT: completed_scenario},
    )
    incident = await lab.start(ScenarioType.BAD_DEPLOYMENT, 3)
    await asyncio.wait_for(store.resolved.wait(), timeout=1)

    detail = await lab.get_incident(incident.incident_id)

    assert detail.status is IncidentStatus.RESOLVED
    assert detail.events[-1].event_type == "incident_recovered"


@pytest.mark.asyncio
async def test_bad_deployment_exercises_fixed_workload_and_records_real_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = BadDeploymentStore()
    lab = IncidentLab(
        FailingWorkloadDatabase(),  # type: ignore[arg-type]
        store,
        Settings(),
    )
    caplog.set_level(logging.INFO, logger="app.lab")

    incident = await lab.start(ScenarioType.BAD_DEPLOYMENT, 10)
    await asyncio.wait_for(store.failure_observed.wait(), timeout=1)
    detail = await lab.get_incident(incident.incident_id)

    failed_events = [
        event for event in detail.events if event.event_type == "request_failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0].details == {
        "path": "/demo/workload",
        "deployment_version": "v2-bad",
        "error_type": "UndefinedColumn",
    }
    error_logs = [
        record
        for record in caplog.records
        if getattr(record, "event_type", None) == "application_error"
    ]
    assert len(error_logs) == 1
    assert error_logs[0].incident_id == incident.incident_id
    assert error_logs[0].error_type == "UndefinedColumn"

    recovered = await lab.recover(incident.incident_id)
    assert recovered.status is IncidentStatus.RESOLVED
    assert store.active_deployment == "v1"


def test_pool_state_is_bounded_and_structured() -> None:
    lab = IncidentLab(FakeDatabase(), FakeStore(), Settings())  # type: ignore[arg-type]

    assert lab.pool_state().model_dump() == {
        "size": 3,
        "available": 2,
        "waiting": 0,
        "maximum": 3,
    }


@pytest.mark.asyncio
async def test_remediation_rejects_non_active_or_wrong_scenario_incident() -> None:
    lab = IncidentLab(FakeDatabase(), FakeStore(), Settings())  # type: ignore[arg-type]
    incident_id = uuid4()

    with pytest.raises(RemediationPreconditionError):
        await lab.release_demo_pool_pressure(incident_id)

    lab._active_id = incident_id
    lab._active_scenario = ScenarioType.BAD_DEPLOYMENT
    with pytest.raises(RemediationPreconditionError):
        await lab.release_demo_pool_pressure(incident_id)


@pytest.mark.asyncio
async def test_deployment_rollback_requires_active_bad_release() -> None:
    incident_id = uuid4()
    lab = IncidentLab(FakeDatabase(), FakeStore(), Settings())  # type: ignore[arg-type]
    lab._active_id = incident_id
    lab._active_scenario = ScenarioType.BAD_DEPLOYMENT

    with pytest.raises(RemediationPreconditionError):
        await lab.rollback_demo_deployment(incident_id)
