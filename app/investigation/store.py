from datetime import UTC, datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from app.database import Database
from app.investigation.models import (
    InvestigationRecord,
    InvestigationReport,
    ToolActivity,
)


class InvestigationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def start(
        self, investigation_id: UUID, incident_id: UUID, model: str
    ) -> None:
        async with self._database.control_connection() as connection:
            await connection.execute(
                """
                INSERT INTO investigations
                    (investigation_id, incident_id, status, started_at, model)
                VALUES (%s, %s, 'running', %s, %s)
                """,
                (investigation_id, incident_id, datetime.now(UTC), model),
            )
            await connection.commit()

    async def complete(
        self, investigation_id: UUID, report: InvestigationReport
    ) -> None:
        async with self._database.control_connection() as connection:
            await connection.execute(
                """
                UPDATE investigations
                SET status = 'completed', completed_at = %s, report = %s,
                    tool_trace = %s
                WHERE investigation_id = %s
                """,
                (
                    report.generated_at,
                    Jsonb(report.model_dump(mode="json")),
                    Jsonb(
                        [item.model_dump(mode="json") for item in report.activity_trace]
                    ),
                    investigation_id,
                ),
            )
            await connection.commit()

    async def fail(
        self,
        investigation_id: UUID,
        error_type: str,
        trace: list[ToolActivity],
    ) -> None:
        async with self._database.control_connection() as connection:
            await connection.execute(
                """
                UPDATE investigations
                SET status = 'failed', completed_at = %s, error_type = %s,
                    tool_trace = %s
                WHERE investigation_id = %s
                """,
                (
                    datetime.now(UTC),
                    error_type,
                    Jsonb([item.model_dump(mode="json") for item in trace]),
                    investigation_id,
                ),
            )
            await connection.commit()

    async def get(self, investigation_id: UUID) -> InvestigationRecord | None:
        async with self._database.control_connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM investigations WHERE investigation_id = %s",
                (investigation_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return InvestigationRecord(**row)

    async def list_for_incident(
        self, incident_id: UUID, limit: int = 20
    ) -> list[InvestigationRecord]:
        bounded = min(max(limit, 1), 20)
        async with self._database.control_connection() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM investigations WHERE incident_id = %s
                ORDER BY started_at DESC LIMIT %s
                """,
                (incident_id, bounded),
            )
            rows = await cursor.fetchall()
        return [InvestigationRecord(**row) for row in rows]
