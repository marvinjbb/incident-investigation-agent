from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings, get_settings


class Database:
    """Own the bounded workload pool and separate incident-control connections."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = AsyncConnectionPool(
            kwargs=settings.database_connection_kwargs,
            min_size=1,
            max_size=settings.database_pool_size,
            timeout=settings.database_pool_timeout_seconds,
            open=False,
        )

    async def open(self) -> None:
        await self.pool.open(
            wait=True, timeout=self.settings.database_connect_timeout_seconds
        )

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def control_connection(
        self, *, autocommit: bool = False
    ) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        connection = await psycopg.AsyncConnection.connect(
            **self.settings.database_connection_kwargs,
            autocommit=autocommit,
            row_factory=dict_row,
        )
        try:
            yield connection
        finally:
            await connection.close()

    async def initialize_schema(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "db" / "init.sql"
        async with self.control_connection(autocommit=True) as connection:
            await connection.execute(
                schema_path.read_text(encoding="utf-8"), prepare=False
            )

    async def recover_stale_state(self) -> None:
        async with self.control_connection() as connection:
            await connection.execute(
                """
                UPDATE incidents
                SET status = 'resolved', ended_at = now()
                WHERE status IN ('starting', 'active', 'recovering')
                """
            )
            await connection.execute(
                "UPDATE application_state SET active_version = 'v1' "
                "WHERE singleton = true"
            )
            await connection.commit()

    def pool_state(self) -> dict[str, int]:
        stats = self.pool.get_stats()
        return {
            "size": stats.get("pool_size", 0),
            "available": stats.get("pool_available", 0),
            "waiting": stats.get("requests_waiting", 0),
            "maximum": self.pool.max_size,
        }


database = Database(get_settings())


async def check_database(settings: Settings) -> None:
    """Open a short-lived connection and prove PostgreSQL can answer a query."""

    async with (
        await psycopg.AsyncConnection.connect(
            **settings.database_connection_kwargs
        ) as connection,
        connection.cursor() as cursor,
    ):
        await cursor.execute("SELECT 1")
        await cursor.fetchone()


DatabaseCheck = Callable[[], Awaitable[None]]


def get_database_check() -> DatabaseCheck:
    settings = get_settings()

    async def probe() -> None:
        await check_database(settings)

    return probe
