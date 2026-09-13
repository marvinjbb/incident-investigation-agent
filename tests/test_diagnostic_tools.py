import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.lab import IncidentNotFoundError
from app.models import (
    Deployment,
    IncidentDetail,
    IncidentEvent,
    IncidentStatus,
    ScenarioType,
)
from app.tools.common import sanitize_value
from app.tools.database import (
    BLOCKING_DIAGNOSTIC_SQL,
    CONNECTION_DIAGNOSTIC_SQL,
    ApplicationPoolDiagnosticTool,
    PostgreSQLDiagnosticTool,
)
from app.tools.deployments import DeploymentDiagnosticTool
from app.tools.incidents import IncidentEventTool, IncidentMetadataTool
from app.tools.logs import ApplicationLogDiagnosticTool
from app.tools.runbooks import (
    RUNBOOK_FILES,
    RunbookDiagnosticTool,
    RunbookNotAllowedError,
)


class FakeStore:
    def __init__(self, incident: IncidentDetail | None) -> None:
        self.incident = incident
        self.deployments = [
            Deployment(
                deployment_id=uuid4(),
                version="v2-bad",
                deployed_at=datetime.now(UTC),
                status="degraded",
                became_active=True,
            )
        ]

    async def get_incident(self, _):
        return self.incident

    async def active_version(self) -> str:
        return "v2-bad"

    async def list_deployments(self) -> list[Deployment]:
        return self.deployments


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, responses) -> None:
        self.responses = responses
        self.executed = []

    async def execute(self, query, params=None):
        self.executed.append((query, params))
        return FakeCursor(self.responses[query])


class FakeDatabase:
    def __init__(self, responses=None) -> None:
        self.connection = FakeConnection(responses or {})

    @asynccontextmanager
    async def control_connection(self):
        yield self.connection

    def pool_state(self) -> dict[str, int]:
        return {"size": 3, "available": 0, "waiting": 1, "maximum": 3}


@pytest.fixture
def incident() -> IncidentDetail:
    incident_id = uuid4()
    now = datetime.now(UTC)
    return IncidentDetail(
        incident_id=incident_id,
        scenario=ScenarioType.BLOCKED_QUERY,
        status=IncidentStatus.ACTIVE,
        started_at=now,
        description="controlled incident",
        events=[
            IncidentEvent(
                event_id=index,
                incident_id=incident_id,
                event_type=f"event_{index}",
                occurred_at=now,
                details={"sequence": index},
            )
            for index in range(1, 5)
        ],
    )


@pytest.mark.asyncio
async def test_incident_metadata_and_events_are_bounded(incident) -> None:
    store = FakeStore(incident)
    retrieved, metadata = await IncidentMetadataTool(store).retrieve(
        incident.incident_id
    )
    events = await IncidentEventTool(store, maximum_limit=2).retrieve(
        incident.incident_id, limit=50
    )

    assert retrieved == incident
    assert metadata.details["status"] == "active"
    assert [item.details["event_id"] for item in events] == [3, 4]


@pytest.mark.asyncio
async def test_unknown_incident_is_rejected() -> None:
    with pytest.raises(IncidentNotFoundError):
        await IncidentMetadataTool(FakeStore(None)).retrieve(uuid4())


@pytest.mark.asyncio
async def test_fixed_blocking_query_returns_sanitized_relationship() -> None:
    row = {
        "blocked_pid": 10,
        "blocking_pid": 20,
        "blocked_state": "active",
        "wait_event_type": "Lock",
        "wait_event": "transactionid",
        "blocked_query_age_seconds": 2.5,
        "blocking_transaction_age_seconds": 3.5,
        "blocked_application_name": "incident-demo-blocked-query",
        "blocking_application_name": "incident-demo-lock-holder",
        "blocked_query": " UPDATE   demo_lock_target SET value = value + 1 ",
        "blocking_query": "UPDATE demo_lock_target SET value = value + 1",
    }
    database = FakeDatabase({BLOCKING_DIAGNOSTIC_SQL: [row]})
    tool = PostgreSQLDiagnosticTool(database, maximum_limit=5)  # type: ignore[arg-type]

    evidence = await tool.blocking_relationships(uuid4(), limit=500)

    assert evidence[0].details["blocked_query"] == (
        "UPDATE demo_lock_target SET value = value + 1"
    )
    assert database.connection.executed == [(BLOCKING_DIAGNOSTIC_SQL, (5,))]


@pytest.mark.asyncio
async def test_connection_diagnostics_distinguish_server_and_pool() -> None:
    database = FakeDatabase(
        {
            CONNECTION_DIAGNOSTIC_SQL: [
                {
                    "total_sessions": 6,
                    "active_sessions": 2,
                    "idle_sessions": 4,
                    "demo_sessions": 3,
                    "configured_limit": 100,
                }
            ]
        }
    )
    incident_id = uuid4()
    server = await PostgreSQLDiagnosticTool(  # type: ignore[arg-type]
        database, 5
    ).connection_utilization(incident_id)
    pool = ApplicationPoolDiagnosticTool(  # type: ignore[arg-type]
        database, Settings(database_pool_size=3)
    ).retrieve(incident_id)

    assert server.details["utilization_percent"] == 6.0
    assert pool.details["checked_out"] == 3
    assert pool.details["available"] == 0


@pytest.mark.asyncio
async def test_deployment_history_is_bounded_and_marks_active(incident) -> None:
    evidence = await DeploymentDiagnosticTool(
        FakeStore(incident), maximum_limit=1
    ).retrieve(incident.incident_id, limit=99)

    assert len(evidence) == 1
    assert evidence[0].details["version"] == "v2-bad"
    assert evidence[0].details["is_currently_active"] is True


def test_log_retrieval_is_fixed_bounded_and_sanitized(tmp_path: Path) -> None:
    incident_id = uuid4()
    log_path = tmp_path / "application.jsonl"
    entries = [
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": "ERROR",
            "event_type": "application_error",
            "incident_id": str(incident_id),
            "error_type": "PoolTimeout",
            "password": "must-not-leak",
        },
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": "INFO",
            "event_type": "unrelated",
            "incident_id": str(uuid4()),
        },
    ]
    log_path.write_text(
        "\n".join(json.dumps(item) for item in entries), encoding="utf-8"
    )
    settings = Settings(
        log_path=str(log_path), diagnostic_result_limit=1, log_backup_count=0
    )

    evidence = ApplicationLogDiagnosticTool(settings).retrieve(incident_id, limit=999)

    assert len(evidence) == 1
    assert evidence[0].details["password"] == "[REDACTED]"
    assert str(tmp_path) not in (evidence[0].reference or "")


def test_runbook_retrieval_is_allowlisted(incident) -> None:
    evidence = RunbookDiagnosticTool().retrieve(
        incident.incident_id, ScenarioType.BLOCKED_QUERY
    )

    assert evidence.reference == "runbook:blocked-query.md"
    assert set(RUNBOOK_FILES) == set(ScenarioType)
    assert "Human approval required" in evidence.details["content"]
    assert all(".." not in filename for filename in RUNBOOK_FILES.values())

    with pytest.raises(RunbookNotAllowedError):
        RunbookDiagnosticTool().retrieve(  # type: ignore[arg-type]
            incident.incident_id, "../../secrets"
        )


def test_recursive_secret_sanitization() -> None:
    safe = sanitize_value(
        {
            "api_token": "hidden",
            "nested": {"database_url": "postgresql://user:pass@db/lab"},
        }
    )

    assert safe == {
        "api_token": "[REDACTED]",
        "nested": {"database_url": "[REDACTED]"},
    }
