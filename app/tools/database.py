from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.config import Settings
from app.database import Database
from app.evidence import EvidenceItem, EvidenceSource, EvidenceType, make_evidence_id
from app.tools.common import sanitize_query

BLOCKING_DIAGNOSTIC_SQL = """
SELECT
    blocked.pid AS blocked_pid,
    blocker.pid AS blocking_pid,
    blocked.state AS blocked_state,
    blocked.wait_event_type,
    blocked.wait_event,
    EXTRACT(EPOCH FROM (clock_timestamp() - blocked.query_start))::double precision
        AS blocked_query_age_seconds,
    EXTRACT(EPOCH FROM (clock_timestamp() - blocker.xact_start))::double precision
        AS blocking_transaction_age_seconds,
    blocked.application_name AS blocked_application_name,
    blocker.application_name AS blocking_application_name,
    blocked.query AS blocked_query,
    blocker.query AS blocking_query
FROM pg_stat_activity AS blocked
CROSS JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS blocking_pid
JOIN pg_stat_activity AS blocker ON blocker.pid = blocking_pid
WHERE blocked.application_name = 'incident-demo-blocked-query'
  AND blocker.application_name = 'incident-demo-lock-holder'
ORDER BY blocked.query_start
LIMIT %s
"""

CONNECTION_DIAGNOSTIC_SQL = """
SELECT
    count(*)::integer AS total_sessions,
    count(*) FILTER (WHERE state = 'active')::integer AS active_sessions,
    count(*) FILTER (WHERE state = 'idle')::integer AS idle_sessions,
    count(*) FILTER (WHERE application_name LIKE 'incident-demo-%')::integer
        AS demo_sessions,
    current_setting('max_connections')::integer AS configured_limit
FROM pg_stat_activity
WHERE datname = current_database()
"""


class PostgreSQLDiagnosticTool:
    def __init__(self, database: Database, maximum_limit: int) -> None:
        self._database = database
        self._maximum_limit = maximum_limit

    async def blocking_relationships(
        self, incident_id: UUID, limit: int | None = None
    ) -> list[EvidenceItem]:
        bounded = min(max(limit or self._maximum_limit, 1), self._maximum_limit)
        async with self._database.control_connection() as connection:
            cursor = await connection.execute(BLOCKING_DIAGNOSTIC_SQL, (bounded,))
            rows = await cursor.fetchall()
        timestamp = datetime.now(UTC)
        evidence: list[EvidenceItem] = []
        for row in rows:
            details = {
                "blocked_pid": row["blocked_pid"],
                "blocking_pid": row["blocking_pid"],
                "blocked_state": row["blocked_state"],
                "wait_event_type": row["wait_event_type"],
                "wait_event": row["wait_event"],
                "blocked_query_age_seconds": row["blocked_query_age_seconds"],
                "blocking_transaction_age_seconds": row[
                    "blocking_transaction_age_seconds"
                ],
                "blocked_application_name": row["blocked_application_name"],
                "blocking_application_name": row["blocking_application_name"],
                "blocked_query": sanitize_query(row["blocked_query"]),
                "blocking_query": sanitize_query(row["blocking_query"]),
            }
            reference = (
                f"postgresql:blocking:{row['blocked_pid']}:{row['blocking_pid']}"
            )
            evidence.append(
                EvidenceItem(
                    evidence_id=make_evidence_id(
                        EvidenceSource.POSTGRESQL,
                        EvidenceType.BLOCKED_SESSION,
                        reference,
                        details,
                    ),
                    source=EvidenceSource.POSTGRESQL,
                    evidence_type=EvidenceType.BLOCKED_SESSION,
                    timestamp=timestamp,
                    incident_id=incident_id,
                    summary=(
                        f"PostgreSQL session {row['blocked_pid']} is blocked by "
                        f"session {row['blocking_pid']}."
                    ),
                    details=details,
                    reference=reference,
                )
            )
        return evidence

    async def connection_utilization(self, incident_id: UUID) -> EvidenceItem:
        async with self._database.control_connection() as connection:
            cursor = await connection.execute(CONNECTION_DIAGNOSTIC_SQL)
            row: dict[str, Any] | None = await cursor.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL connection diagnostics unavailable")
        total = int(row["total_sessions"])
        configured = int(row["configured_limit"])
        details = {
            **row,
            "utilization_percent": round(total / configured * 100, 2)
            if configured
            else None,
        }
        return EvidenceItem(
            evidence_id=make_evidence_id(
                EvidenceSource.POSTGRESQL,
                EvidenceType.CONNECTION_UTILIZATION,
                "postgresql:connections",
                details,
            ),
            source=EvidenceSource.POSTGRESQL,
            evidence_type=EvidenceType.CONNECTION_UTILIZATION,
            timestamp=datetime.now(UTC),
            incident_id=incident_id,
            summary=(
                f"PostgreSQL is using {total} of {configured} configured connections."
            ),
            details=details,
            reference="postgresql:connections",
        )


class ApplicationPoolDiagnosticTool:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings

    def retrieve(self, incident_id: UUID) -> EvidenceItem:
        state = self._database.pool_state()
        details = {
            "configured_pool_size": self._settings.database_pool_size,
            "current_pool_size": state["size"],
            "checked_out": max(state["size"] - state["available"], 0),
            "available": state["available"],
            "waiting_requests": state["waiting"],
            "pool_timeout_seconds": self._settings.database_pool_timeout_seconds,
        }
        return EvidenceItem(
            evidence_id=make_evidence_id(
                EvidenceSource.CONNECTION_POOL,
                EvidenceType.POOL_STATE,
                "application-pool",
                details,
            ),
            source=EvidenceSource.CONNECTION_POOL,
            evidence_type=EvidenceType.POOL_STATE,
            timestamp=datetime.now(UTC),
            incident_id=incident_id,
            summary=(
                f"Application pool has {details['checked_out']} checked out and "
                f"{details['available']} available connections."
            ),
            details=details,
            reference="application-pool",
        )
