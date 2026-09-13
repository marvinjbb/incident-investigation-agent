import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings
from app.database import DatabaseCheck, get_database_check

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str


class DatabaseHealthResponse(BaseModel):
    status: str
    database: str


settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/health/db", response_model=DatabaseHealthResponse)
async def database_health(
    database_check: Annotated[DatabaseCheck, Depends(get_database_check)],
) -> DatabaseHealthResponse:
    try:
        await database_check()
    except Exception as exc:
        logger.warning("Database health check failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc

    return DatabaseHealthResponse(status="ok", database="reachable")
