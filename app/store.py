from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.database import Database
from app.models import (
    Deployment,
    Incident,
    IncidentDetail,
    IncidentEvent,
    IncidentStatus,
    ScenarioType,
)


class IncidentStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_incident(
        self, incident_id: UUID, scenario: ScenarioType, description: str
    ) -> Incident:
        started_at = datetime.now(UTC)
        async with self.database.control_connection() as connection:
            await connection.execute(
                """
                INSERT INTO incidents
                    (incident_id, scenario, status, started_at, description)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    incident_id,
                    scenario.value,
                    IncidentStatus.STARTING.value,
                    started_at,
                    description,
                ),
            )
            await connection.execute(
                """
                DELETE FROM incidents
                WHERE incident_id IN (
                    SELECT incident_id FROM incidents
                    WHERE status IN ('resolved', 'failed')
                    ORDER BY started_at DESC
                    OFFSET %s
                )
                """,
                (self.database.settings.incident_history_limit,),
            )
            await connection.commit()
        return Incident(
            incident_id=incident_id,
            scenario=scenario,
            status=IncidentStatus.STARTING,
            started_at=started_at,
            description=description,
        )

    async def update_status(self, incident_id: UUID, status: IncidentStatus) -> None:
        ended_at = (
            datetime.now(UTC)
            if status in {IncidentStatus.RESOLVED, IncidentStatus.FAILED}
            else None
        )
        async with self.database.control_connection() as connection:
            await connection.execute(
                "UPDATE incidents SET status = %s, ended_at = %s "
                "WHERE incident_id = %s",
                (status.value, ended_at, incident_id),
            )
            await connection.commit()

    async def add_event(
        self,
        incident_id: UUID,
        event_type: str,
        details: dict[str, object] | None = None,
    ) -> None:
        async with self.database.control_connection() as connection:
            await connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, details)
                VALUES (%s, %s, %s)
                """,
                (incident_id, event_type, Jsonb(details or {})),
            )
            await connection.commit()

    async def get_incident(self, incident_id: UUID) -> IncidentDetail | None:
        async with self.database.control_connection() as connection:
            incident_cursor = await connection.execute(
                "SELECT * FROM incidents WHERE incident_id = %s", (incident_id,)
            )
            incident = await incident_cursor.fetchone()
            if incident is None:
                return None
            event_cursor = await connection.execute(
                """
                SELECT event_id, incident_id, event_type, occurred_at, details
                FROM incident_events
                WHERE incident_id = %s
                ORDER BY occurred_at, event_id
                """,
                (incident_id,),
            )
            events = await event_cursor.fetchall()
        return IncidentDetail(
            **incident,
            events=[IncidentEvent(**event) for event in events],
        )

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
        async with self.database.control_connection() as connection:
            await connection.execute(
                """
                INSERT INTO deployments
                    (deployment_id, version, deployed_at, status, became_active)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    deployment.deployment_id,
                    deployment.version,
                    deployment.deployed_at,
                    deployment.status,
                    deployment.became_active,
                ),
            )
            if became_active:
                await connection.execute(
                    "UPDATE application_state SET active_version = %s "
                    "WHERE singleton = true",
                    (version,),
                )
            await connection.execute(
                """
                DELETE FROM deployments
                WHERE deployment_id IN (
                    SELECT deployment_id FROM deployments
                    ORDER BY deployed_at DESC
                    OFFSET %s
                )
                """,
                (self.database.settings.deployment_history_limit,),
            )
            await connection.commit()
        return deployment

    async def active_version(self) -> str:
        async with self.database.control_connection() as connection:
            cursor = await connection.execute(
                "SELECT active_version FROM application_state WHERE singleton = true"
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Application state is unavailable")
        return str(row["active_version"])

    async def list_deployments(self) -> list[Deployment]:
        async with self.database.control_connection() as connection:
            cursor = await connection.execute(
                """
                SELECT deployment_id, version, deployed_at, status, became_active
                FROM deployments ORDER BY deployed_at DESC
                """
            )
            rows = await cursor.fetchall()
        return [Deployment(**row) for row in rows]
