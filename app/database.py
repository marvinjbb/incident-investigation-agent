from collections.abc import Awaitable, Callable

import psycopg

from app.config import Settings, get_settings


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
