from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.database import Database


class DemoSessionExpiredError(RuntimeError):
    pass


class PublicRateLimitError(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


class PublicStore:
    def __init__(self, database: Database, ttl_seconds: int) -> None:
        self.database = database
        self.ttl_seconds = ttl_seconds

    async def create_session(self) -> UUID:
        session_id = uuid4()
        now = datetime.now(UTC)
        async with self.database.control_connection() as connection:
            await connection.execute(
                """
                INSERT INTO demo_sessions
                    (session_id, created_at, expires_at, last_seen_at)
                VALUES (%s, %s, %s, %s)
                """,
                (session_id, now, now + timedelta(seconds=self.ttl_seconds), now),
            )
            await connection.commit()
        return session_id

    async def require_session(self, session_id: UUID) -> None:
        async with self.database.control_connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE demo_sessions SET last_seen_at = now()
                WHERE session_id = %s AND expires_at > now()
                RETURNING session_id
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
            await connection.commit()
        if row is None:
            raise DemoSessionExpiredError

    async def owns_incident(self, session_id: UUID, incident_id: UUID) -> bool:
        return await self._exists(
            "SELECT 1 FROM incidents WHERE session_id=%s AND incident_id=%s",
            (session_id, incident_id),
        )

    async def owns_investigation(
        self, session_id: UUID, investigation_id: UUID
    ) -> bool:
        return await self._exists(
            """
            SELECT 1 FROM investigations x
            JOIN incidents i USING (incident_id)
            WHERE i.session_id = %s AND x.investigation_id = %s
            """,
            (session_id, investigation_id),
        )

    async def owns_proposal(self, session_id: UUID, proposal_id: UUID) -> bool:
        return await self._exists(
            """
            SELECT 1 FROM remediation_proposals r
            JOIN incidents i USING (incident_id)
            WHERE i.session_id = %s AND r.proposal_id = %s
            """,
            (session_id, proposal_id),
        )

    async def consume_rate_limit(
        self, rate_key: str, action: str, limit: int, window_seconds: int
    ) -> None:
        async with self.database.control_connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"{rate_key}:{action}",),
            )
            cursor = await connection.execute(
                """
                SELECT count(*) AS count, min(occurred_at) AS oldest
                FROM public_rate_limit_events
                WHERE rate_key = %s AND action = %s
                  AND occurred_at > now() - (%s * interval '1 second')
                """,
                (rate_key, action, window_seconds),
            )
            row = await cursor.fetchone()
            if row["count"] >= limit:
                retry = max(
                    1,
                    window_seconds
                    - int((datetime.now(UTC) - row["oldest"]).total_seconds()),
                )
                raise PublicRateLimitError(retry)
            await connection.execute(
                """
                INSERT INTO public_rate_limit_events (rate_key, action)
                VALUES (%s, %s)
                """,
                (rate_key, action),
            )
            await connection.commit()

    async def cleanup(self) -> None:
        async with self.database.control_connection() as connection:
            await connection.execute(
                """
                UPDATE remediation_proposals
                SET status = 'expired', completed_at = now()
                WHERE status IN ('pending_approval', 'approved')
                  AND expires_at < now()
                """
            )
            await connection.execute(
                """
                DELETE FROM public_rate_limit_events
                WHERE occurred_at < now() - interval '1 day'
                """
            )
            await connection.execute(
                "DELETE FROM demo_sessions WHERE expires_at < now()-interval '1 hour'"
            )
            await connection.commit()

    async def _exists(self, query: str, params: tuple[UUID, UUID]) -> bool:
        async with self.database.control_connection() as connection:
            cursor = await connection.execute(query, params)
            return await cursor.fetchone() is not None
