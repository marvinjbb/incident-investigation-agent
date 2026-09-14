from contextlib import asynccontextmanager

import pytest

from app.database import Database


class Cursor:
    async def fetchone(self):
        return {"terminated": True}


class Connection:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return Cursor()


class ControlledDatabase:
    def __init__(self):
        self.connection = Connection()

    @asynccontextmanager
    async def control_connection(self, *, autocommit=False):
        yield self.connection


@pytest.mark.asyncio
async def test_blocker_termination_uses_fixed_ownership_query_without_pid_input():
    database = ControlledDatabase()

    terminated = await Database.terminate_controlled_blocker(database)  # type: ignore[arg-type]

    sql, params = database.connection.calls[0]
    assert terminated is True
    assert params is None
    assert "incident-demo-blocked-query" in sql
    assert "incident-demo-lock-holder" in sql
    assert "pg_terminate_backend(pid)" in sql
