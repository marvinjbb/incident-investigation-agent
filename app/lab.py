import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol
from uuid import UUID, uuid4

from psycopg import errors
from psycopg_pool import PoolTimeout

from app.config import Settings
from app.database import Database
from app.logging_config import log_event
from app.models import (
    Deployment,
    Incident,
    IncidentDetail,
    IncidentStatus,
    PoolState,
    ScenarioType,
    WorkloadResponse,
)
from app.store import IncidentStore

logger = logging.getLogger(__name__)


class ActiveIncidentError(RuntimeError):
    pass


class IncidentNotFoundError(RuntimeError):
    pass


class WorkloadUnavailableError(RuntimeError):
    pass


class RemediationPreconditionError(RuntimeError):
    pass


class Store(Protocol):
    async def create_incident(
        self, incident_id: UUID, scenario: ScenarioType, description: str
    ) -> Incident: ...

    async def update_status(
        self, incident_id: UUID, status: IncidentStatus
    ) -> None: ...

    async def add_event(
        self,
        incident_id: UUID,
        event_type: str,
        details: dict[str, object] | None = None,
    ) -> None: ...

    async def get_incident(self, incident_id: UUID) -> IncidentDetail | None: ...

    async def activate_deployment(
        self, version: str, status: str, became_active: bool = True
    ) -> Deployment: ...

    async def active_version(self) -> str: ...

    async def list_deployments(self) -> list[Deployment]: ...


ScenarioHandler = Callable[[UUID, asyncio.Event, int], Awaitable[None]]

SCENARIO_DESCRIPTIONS = {
    ScenarioType.BLOCKED_QUERY: (
        "A transaction holds a row lock while a second update blocks."
    ),
    ScenarioType.CONNECTION_EXHAUSTION: (
        "The bounded application pool is temporarily saturated."
    ),
    ScenarioType.BAD_DEPLOYMENT: (
        "An incompatible release causes the demo workload to fail."
    ),
}


class IncidentLab:
    """Run one bounded, allowlisted incident and guarantee cleanup."""

    def __init__(
        self,
        database: Database,
        store: Store,
        settings: Settings,
        handlers: dict[ScenarioType, ScenarioHandler] | None = None,
    ) -> None:
        self.database = database
        self.store = store
        self.settings = settings
        self._lock = asyncio.Lock()
        self._active_id: UUID | None = None
        self._active_scenario: ScenarioType | None = None
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._handlers = handlers or {
            ScenarioType.BLOCKED_QUERY: self._run_blocked_query,
            ScenarioType.CONNECTION_EXHAUSTION: self._run_connection_exhaustion,
            ScenarioType.BAD_DEPLOYMENT: self._run_bad_deployment,
        }

    async def start(self, scenario: ScenarioType, duration_seconds: int) -> Incident:
        async with self._lock:
            if self._active_id is not None:
                raise ActiveIncidentError("A demo incident is already active")
            incident_id = uuid4()
            incident = await self.store.create_incident(
                incident_id, scenario, SCENARIO_DESCRIPTIONS[scenario]
            )
            await self.store.add_event(
                incident_id,
                "incident_started",
                {
                    "scenario": scenario.value,
                    "maximum_duration_seconds": duration_seconds,
                },
            )
            self._active_id = incident_id
            self._active_scenario = scenario
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(
                self._run(incident_id, scenario, self._stop_event, duration_seconds),
                name=f"incident-{incident_id}",
            )
        log_event(
            logger,
            "incident_started",
            "Demo incident accepted",
            incident_id=incident_id,
            scenario=scenario.value,
        )
        return incident

    async def _run(
        self,
        incident_id: UUID,
        scenario: ScenarioType,
        stop_event: asyncio.Event,
        duration_seconds: int,
    ) -> None:
        failed = False
        try:
            await self.store.update_status(incident_id, IncidentStatus.ACTIVE)
            await self._handlers[scenario](incident_id, stop_event, duration_seconds)
        except Exception as exc:
            failed = True
            await self.store.add_event(
                incident_id, "incident_failed", {"error_type": type(exc).__name__}
            )
            await self.store.update_status(incident_id, IncidentStatus.FAILED)
            log_event(
                logger,
                "incident_failed",
                "Demo incident failed safely",
                incident_id=incident_id,
                scenario=scenario.value,
                error_type=type(exc).__name__,
            )
        finally:
            if not failed:
                await self.store.add_event(incident_id, "incident_recovered")
                await self.store.update_status(incident_id, IncidentStatus.RESOLVED)
                log_event(
                    logger,
                    "incident_recovered",
                    "Demo incident recovered",
                    incident_id=incident_id,
                    scenario=scenario.value,
                )
            async with self._lock:
                if self._active_id == incident_id:
                    self._active_id = None
                    self._active_scenario = None
                    self._stop_event = None
                    self._task = None

    async def recover(self, incident_id: UUID) -> IncidentDetail:
        async with self._lock:
            if self._active_id != incident_id or self._stop_event is None:
                incident = await self.store.get_incident(incident_id)
                if incident is None:
                    raise IncidentNotFoundError("Incident not found")
                return incident
            stop_event = self._stop_event
            task = self._task
            await self.store.update_status(incident_id, IncidentStatus.RECOVERING)
            await self.store.add_event(incident_id, "recovery_requested")
            stop_event.set()
        if task is not None:
            await task
        incident = await self.store.get_incident(incident_id)
        if incident is None:
            raise IncidentNotFoundError("Incident not found")
        return incident

    async def terminate_demo_blocker(self, incident_id: UUID) -> None:
        await self._require_active(incident_id, ScenarioType.BLOCKED_QUERY)
        if not await self.database.terminate_controlled_blocker():
            raise RemediationPreconditionError(
                "Controlled blocking relationship is no longer present"
            )
        await self.recover(incident_id)

    async def release_demo_pool_pressure(self, incident_id: UUID) -> None:
        await self._require_active(incident_id, ScenarioType.CONNECTION_EXHAUSTION)
        state = self.pool_state()
        if state.available > 0:
            raise RemediationPreconditionError(
                "Controlled pool pressure is no longer present"
            )
        await self.recover(incident_id)

    async def rollback_demo_deployment(self, incident_id: UUID) -> None:
        await self._require_active(incident_id, ScenarioType.BAD_DEPLOYMENT)
        if await self.store.active_version() != "v2-bad":
            raise RemediationPreconditionError(
                "The controlled bad release is not active"
            )
        history = await self.store.list_deployments()
        if not any(
            item.version == "v1" and item.status == "healthy" for item in history
        ):
            raise RemediationPreconditionError(
                "No approved healthy demo release exists"
            )
        await self.store.add_event(
            incident_id,
            "remediation_rollback_requested",
            {"from_version": "v2-bad", "to_version": "v1"},
        )
        await self.recover(incident_id)

    async def _require_active(self, incident_id: UUID, scenario: ScenarioType) -> None:
        async with self._lock:
            if self._active_id != incident_id or self._active_scenario is not scenario:
                raise RemediationPreconditionError(
                    "The controlled incident is no longer active"
                )

    async def shutdown(self) -> None:
        async with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
            task = self._task
        if task is not None:
            await task

    async def get_incident(self, incident_id: UUID) -> IncidentDetail:
        incident = await self.store.get_incident(incident_id)
        if incident is None:
            raise IncidentNotFoundError("Incident not found")
        return incident

    async def workload(self, path: str) -> WorkloadResponse:
        version = await self.store.active_version()
        try:
            async with self.database.pool.connection() as connection:
                if version == "v2-bad":
                    await connection.execute(
                        "SELECT removed_message FROM demo_workload WHERE id = 1"
                    )
                cursor = await connection.execute(
                    "SELECT message FROM demo_workload WHERE id = 1"
                )
                row = await cursor.fetchone()
        except (PoolTimeout, errors.UndefinedColumn) as exc:
            if self._active_id is not None:
                await self.store.add_event(
                    self._active_id,
                    "request_failed",
                    {
                        "path": path,
                        "deployment_version": version,
                        "error_type": type(exc).__name__,
                    },
                )
            log_event(
                logger,
                "application_error",
                "Demo workload request failed",
                incident_id=self._active_id,
                scenario=self._active_scenario,
                path=path,
                deployment_version=version,
                error_type=type(exc).__name__,
            )
            raise WorkloadUnavailableError(
                "Demo workload is temporarily unavailable"
            ) from exc
        if row is None:
            raise WorkloadUnavailableError("Demo workload is temporarily unavailable")
        return WorkloadResponse(status="ok", version=version, message=str(row[0]))

    def pool_state(self) -> PoolState:
        return PoolState(**self.database.pool_state())

    async def deployments(self) -> list[Deployment]:
        return await self.store.list_deployments()

    async def _wait(self, stop_event: asyncio.Event, duration_seconds: int) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=duration_seconds)

    async def _run_blocked_query(
        self, incident_id: UUID, stop_event: asyncio.Event, duration_seconds: int
    ) -> None:
        async with (
            self.database.control_connection() as locking_connection,
            self.database.control_connection() as blocked_connection,
        ):
            await locking_connection.execute(
                "SET application_name = 'incident-demo-lock-holder'"
            )
            await blocked_connection.execute(
                "SET application_name = 'incident-demo-blocked-query'"
            )
            await locking_connection.execute(
                "UPDATE demo_lock_target SET value = value + 1 WHERE id = 1"
            )
            await self.store.add_event(incident_id, "lock_acquired", {"row_id": 1})
            blocked_task = asyncio.create_task(
                blocked_connection.execute(
                    "UPDATE demo_lock_target SET value = value + 1 WHERE id = 1"
                )
            )
            try:
                observed = await self._wait_for_blocking_relationship()
                if not observed:
                    raise RuntimeError("Blocking relationship was not observed")
                await self.store.add_event(
                    incident_id,
                    "query_blocked",
                    {"blocked_application": "demo-workload"},
                )
                await self._wait(stop_event, duration_seconds)
            finally:
                with suppress(Exception):
                    await locking_connection.rollback()
                with suppress(Exception):
                    await blocked_task
                with suppress(Exception):
                    await blocked_connection.rollback()

    async def _wait_for_blocking_relationship(self) -> bool:
        for _ in range(20):
            async with self.database.control_connection() as connection:
                cursor = await connection.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pg_stat_activity
                        WHERE application_name = 'incident-demo-blocked-query'
                        AND cardinality(pg_blocking_pids(pid)) > 0
                    ) AS blocked
                    """
                )
                row = await cursor.fetchone()
            if row and row["blocked"]:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _run_connection_exhaustion(
        self, incident_id: UUID, stop_event: asyncio.Event, duration_seconds: int
    ) -> None:
        held_connections = []
        try:
            for _ in range(self.settings.database_pool_size):
                held_connections.append(await self.database.pool.getconn())
            await self.store.add_event(
                incident_id,
                "pool_saturated",
                {"held_connections": len(held_connections)},
            )
            await self._wait(stop_event, duration_seconds)
        finally:
            for connection in held_connections:
                await self.database.pool.putconn(connection)

    async def _run_bad_deployment(
        self, incident_id: UUID, stop_event: asyncio.Event, duration_seconds: int
    ) -> None:
        await self.store.add_event(
            incident_id, "deployment_started", {"version": "v2-bad"}
        )
        deployment = await self.store.activate_deployment("v2-bad", "degraded")
        await self.store.add_event(
            incident_id,
            "deployment_activated",
            {
                "version": deployment.version,
                "deployment_id": str(deployment.deployment_id),
            },
        )
        try:
            await self._wait(stop_event, duration_seconds)
        finally:
            recovery = await self.store.activate_deployment("v1", "healthy")
            await self.store.add_event(
                incident_id,
                "deployment_activated",
                {
                    "version": recovery.version,
                    "deployment_id": str(recovery.deployment_id),
                },
            )


def build_incident_lab(database: Database, settings: Settings) -> IncidentLab:
    return IncidentLab(database, IncidentStore(database), settings)
