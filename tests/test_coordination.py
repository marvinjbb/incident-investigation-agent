from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from app.investigation.store import (
    InvestigationAlreadyRunningError,
    InvestigationStore,
)
from app.lab import ActiveIncidentError
from app.models import ScenarioType
from app.store import IncidentStore


class ConflictingConnection:
    async def execute(self, *_: object, **__: object) -> None:
        raise UniqueViolation


class ConflictingDatabase:
    @asynccontextmanager
    async def control_connection(self):
        yield ConflictingConnection()


@pytest.mark.asyncio
async def test_database_conflict_prevents_second_active_incident() -> None:
    store = IncidentStore(ConflictingDatabase())  # type: ignore[arg-type]

    with pytest.raises(ActiveIncidentError):
        await store.create_incident_atomic(
            uuid4(),
            ScenarioType.BLOCKED_QUERY,
            "synthetic incident",
            uuid4(),
        )


@pytest.mark.asyncio
async def test_database_conflict_prevents_second_running_investigation() -> None:
    store = InvestigationStore(ConflictingDatabase())  # type: ignore[arg-type]

    with pytest.raises(InvestigationAlreadyRunningError):
        await store.start(uuid4(), uuid4(), "bounded-model")
