from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import DatabaseCheck, get_database_check
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    live = await client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_database_health_returns_reachable(client: AsyncClient) -> None:
    async def database_is_reachable() -> None:
        return None

    app.dependency_overrides[get_database_check] = lambda: database_is_reachable

    response = await client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}

    ready = await client.get("/health/ready")
    assert ready.status_code == 200


@pytest.mark.asyncio
async def test_database_health_returns_503_without_details(
    client: AsyncClient,
) -> None:
    async def database_is_unavailable() -> None:
        raise RuntimeError("password=must-not-leak")

    override: DatabaseCheck = database_is_unavailable
    app.dependency_overrides[get_database_check] = lambda: override

    response = await client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
    assert "must-not-leak" not in response.text
