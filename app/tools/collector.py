import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from uuid import UUID

from app.evidence import EvidenceBundle, EvidenceItem
from app.tools.database import (
    ApplicationPoolDiagnosticTool,
    PostgreSQLDiagnosticTool,
)
from app.tools.deployments import DeploymentDiagnosticTool
from app.tools.incidents import IncidentEventTool, IncidentMetadataTool
from app.tools.logs import ApplicationLogDiagnosticTool
from app.tools.runbooks import RunbookDiagnosticTool

logger = logging.getLogger(__name__)


class IncidentEvidenceCollector:
    """Aggregate source facts without ranking them or deciding root cause."""

    def __init__(
        self,
        metadata: IncidentMetadataTool,
        events: IncidentEventTool,
        postgresql: PostgreSQLDiagnosticTool,
        pool: ApplicationPoolDiagnosticTool,
        deployments: DeploymentDiagnosticTool,
        logs: ApplicationLogDiagnosticTool,
        runbooks: RunbookDiagnosticTool,
        maximum_items: int,
    ) -> None:
        self._metadata = metadata
        self._events = events
        self._postgresql = postgresql
        self._pool = pool
        self._deployments = deployments
        self._logs = logs
        self._runbooks = runbooks
        self._maximum_items = maximum_items

    async def collect(self, incident_id: UUID) -> EvidenceBundle:
        incident, metadata = await self._metadata.retrieve(incident_id)
        items: list[EvidenceItem] = [metadata]
        collectors = (
            ("incident_events", self._events.retrieve(incident_id)),
            (
                "postgresql_blocking",
                self._postgresql.blocking_relationships(incident_id),
            ),
            (
                "postgresql_connections",
                self._one(self._postgresql.connection_utilization(incident_id)),
            ),
            ("deployments", self._deployments.retrieve(incident_id)),
        )
        for _source_name, operation in collectors:
            try:
                items.extend(await operation)
            except Exception as exc:
                logger.warning(
                    "Diagnostic source unavailable",
                    extra={
                        "event_type": "diagnostic_source_unavailable",
                        "incident_id": incident_id,
                        "error_type": type(exc).__name__,
                    },
                )
        items.append(self._pool.retrieve(incident_id))
        items.extend(self._logs.retrieve(incident_id))
        items.append(self._runbooks.retrieve(incident_id, incident.scenario))
        return EvidenceBundle(
            incident_id=incident_id,
            scenario=incident.scenario.value,
            collected_at=datetime.now(UTC),
            items=items[: self._maximum_items],
        )

    @staticmethod
    async def _one(operation: Awaitable[EvidenceItem]) -> list[EvidenceItem]:
        return [await operation]
