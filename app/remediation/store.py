from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.database import Database
from app.remediation.models import (
    RemediationAction,
    RemediationAuditEvent,
    RemediationProposal,
    RemediationStatus,
)


class RemediationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, proposal: RemediationProposal) -> None:
        async with self._database.control_connection() as connection:
            await connection.execute(
                """
                INSERT INTO remediation_proposals (
                    proposal_id, incident_id, investigation_id, action_type,
                    status, summary, reason, supporting_evidence_ids,
                    created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    proposal.proposal_id,
                    proposal.incident_id,
                    proposal.investigation_id,
                    proposal.action_type.value,
                    proposal.status.value,
                    proposal.summary,
                    proposal.reason,
                    Jsonb(proposal.supporting_evidence_ids),
                    proposal.created_at,
                    proposal.expires_at,
                ),
            )
            await connection.execute(
                """
                INSERT INTO remediation_audit_events
                    (proposal_id, event_type, details)
                VALUES (%s, 'remediation_proposal_created', %s)
                """,
                (
                    proposal.proposal_id,
                    Jsonb({"action_type": proposal.action_type.value}),
                ),
            )
            await connection.commit()

    async def get(self, proposal_id: UUID) -> RemediationProposal | None:
        async with self._database.control_connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM remediation_proposals WHERE proposal_id = %s",
                (proposal_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            audit_cursor = await connection.execute(
                """
                SELECT audit_event_id, proposal_id, event_type, occurred_at, details
                FROM remediation_audit_events WHERE proposal_id = %s
                ORDER BY occurred_at, audit_event_id LIMIT 20
                """,
                (proposal_id,),
            )
            events = await audit_cursor.fetchall()
        return RemediationProposal(
            **row,
            audit_events=[RemediationAuditEvent(**event) for event in events],
        )

    async def list_for_incident(
        self, incident_id: UUID, limit: int = 20
    ) -> list[RemediationProposal]:
        bounded = min(max(limit, 1), 20)
        async with self._database.control_connection() as connection:
            cursor = await connection.execute(
                """
                SELECT proposal_id FROM remediation_proposals
                WHERE incident_id = %s ORDER BY created_at DESC LIMIT %s
                """,
                (incident_id, bounded),
            )
            rows = await cursor.fetchall()
        proposals = [await self.get(row["proposal_id"]) for row in rows]
        return [item for item in proposals if item is not None]

    async def transition(
        self,
        proposal_id: UUID,
        expected: RemediationStatus,
        target: RemediationStatus,
        event_type: str,
        *,
        execution_result: dict[str, object] | None = None,
        verification_result: dict[str, object] | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        timestamp_column = {
            RemediationStatus.APPROVED: "approved_at",
            RemediationStatus.EXECUTING: "executed_at",
            RemediationStatus.SUCCEEDED: "completed_at",
            RemediationStatus.FAILED: "completed_at",
            RemediationStatus.REJECTED: "completed_at",
            RemediationStatus.EXPIRED: "completed_at",
        }.get(target)
        set_timestamp = f", {timestamp_column} = %s" if timestamp_column else ""
        parameters: list[object] = [target.value]
        if timestamp_column:
            parameters.append(now)
        parameters.extend(
            [
                Jsonb(execution_result) if execution_result is not None else None,
                Jsonb(verification_result) if verification_result is not None else None,
                proposal_id,
                expected.value,
            ]
        )
        async with self._database.control_connection() as connection:
            cursor = await connection.execute(
                f"""
                UPDATE remediation_proposals
                SET status = %s {set_timestamp},
                    execution_result = COALESCE(%s, execution_result),
                    verification_result = COALESCE(%s, verification_result)
                WHERE proposal_id = %s AND status = %s
                RETURNING proposal_id
                """,
                parameters,
            )
            changed = await cursor.fetchone()
            if changed is not None:
                action_type = await self._action_type(connection, proposal_id)
                await connection.execute(
                    """
                    INSERT INTO remediation_audit_events
                        (proposal_id, event_type, details)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        proposal_id,
                        event_type,
                        Jsonb(
                            {"status": target.value, "action_type": action_type.value}
                        ),
                    ),
                )
            await connection.commit()
        return changed is not None

    async def add_audit(
        self, proposal_id: UUID, event_type: str, details: dict[str, object]
    ) -> None:
        async with self._database.control_connection() as connection:
            await connection.execute(
                """
                INSERT INTO remediation_audit_events
                    (proposal_id, event_type, details) VALUES (%s, %s, %s)
                """,
                (proposal_id, event_type, Jsonb(details)),
            )
            await connection.commit()

    async def _action_type(
        self, connection: Any, proposal_id: UUID
    ) -> RemediationAction:
        cursor = await connection.execute(
            "SELECT action_type FROM remediation_proposals WHERE proposal_id = %s",
            (proposal_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Remediation proposal disappeared")
        return RemediationAction(row["action_type"])
