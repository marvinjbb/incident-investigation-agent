import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from app.evidence import EvidenceItem
from app.models import ScenarioType
from app.tools.database import (
    ApplicationPoolDiagnosticTool,
    PostgreSQLDiagnosticTool,
)
from app.tools.deployments import DeploymentDiagnosticTool
from app.tools.incidents import IncidentEventTool, IncidentMetadataTool
from app.tools.logs import ApplicationLogDiagnosticTool
from app.tools.runbooks import RunbookDiagnosticTool


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRegistryError(ValueError):
    pass


ToolHandler = Callable[[], Awaitable[list[EvidenceItem]]]


class InvestigationToolRegistry:
    """Bind a fixed incident ID to the approved read-only diagnostic capabilities."""

    def __init__(
        self,
        incident_id: UUID,
        metadata: IncidentMetadataTool,
        events: IncidentEventTool,
        postgresql: PostgreSQLDiagnosticTool,
        pool: ApplicationPoolDiagnosticTool,
        deployments: DeploymentDiagnosticTool,
        logs: ApplicationLogDiagnosticTool,
        runbooks: RunbookDiagnosticTool,
    ) -> None:
        self.incident_id = incident_id
        self._metadata = metadata
        self._events = events
        self._postgresql = postgresql
        self._pool = pool
        self._deployments = deployments
        self._logs = logs
        self._runbooks = runbooks
        self._handlers: dict[str, ToolHandler] = {
            "get_incident": self._get_incident,
            "get_incident_events": self._get_events,
            "get_application_logs": self._get_logs,
            "get_database_blocking": self._get_blocking,
            "get_database_connections": self._get_connections,
            "get_application_pool_state": self._get_pool,
            "get_recent_deployments": self._get_deployments,
            "get_runbook": self._get_runbook,
        }

    @property
    def definitions(self) -> list[dict[str, Any]]:
        descriptions = {
            "get_incident": "Get immutable incident metadata and current status.",
            "get_incident_events": "Get bounded machine-generated incident events.",
            "get_application_logs": "Get bounded logs for only this incident.",
            "get_database_blocking": (
                "Inspect PostgreSQL blocking only when waits, locks, or blocked "
                "queries are suspected."
            ),
            "get_database_connections": (
                "Inspect PostgreSQL session capacity when connection pressure "
                "must be confirmed or ruled out."
            ),
            "get_application_pool_state": (
                "Inspect the live application pool when pool waits, timeouts, or "
                "application connection pressure are suspected."
            ),
            "get_recent_deployments": (
                "Inspect bounded deployment history when events or errors suggest "
                "a recent application change."
            ),
            "get_runbook": "Get the allowlisted runbook for this incident scenario.",
        }
        schema = ToolArguments.model_json_schema()
        return [
            {
                "type": "function",
                "name": name,
                "description": descriptions[name],
                "parameters": schema,
                "strict": True,
            }
            for name in self._handlers
        ]

    async def execute(self, name: str, raw_arguments: str) -> list[EvidenceItem]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolRegistryError("Unsupported diagnostic tool")
        try:
            arguments = json.loads(raw_arguments)
            ToolArguments.model_validate(arguments)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ToolRegistryError("Invalid diagnostic tool arguments") from exc
        return await handler()

    async def validate_incident(self) -> None:
        await self._metadata.retrieve(self.incident_id)

    async def _get_incident(self) -> list[EvidenceItem]:
        _, evidence = await self._metadata.retrieve(self.incident_id)
        return [evidence]

    async def _get_events(self) -> list[EvidenceItem]:
        return await self._events.retrieve(self.incident_id)

    async def _get_logs(self) -> list[EvidenceItem]:
        return self._logs.retrieve(self.incident_id)

    async def _get_blocking(self) -> list[EvidenceItem]:
        return await self._postgresql.blocking_relationships(self.incident_id)

    async def _get_connections(self) -> list[EvidenceItem]:
        return [await self._postgresql.connection_utilization(self.incident_id)]

    async def _get_pool(self) -> list[EvidenceItem]:
        return [self._pool.retrieve(self.incident_id)]

    async def _get_deployments(self) -> list[EvidenceItem]:
        return await self._deployments.retrieve(self.incident_id)

    async def _get_runbook(self) -> list[EvidenceItem]:
        incident, _ = await self._metadata.retrieve(self.incident_id)
        scenario = ScenarioType(incident.scenario)
        return [self._runbooks.retrieve(self.incident_id, scenario)]
