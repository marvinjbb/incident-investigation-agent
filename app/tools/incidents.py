from typing import Protocol
from uuid import UUID

from app.evidence import EvidenceItem, EvidenceSource, EvidenceType, make_evidence_id
from app.lab import IncidentNotFoundError
from app.models import IncidentDetail
from app.tools.common import sanitize_value


class IncidentReader(Protocol):
    async def get_incident(self, incident_id: UUID) -> IncidentDetail | None: ...


class IncidentMetadataTool:
    def __init__(self, store: IncidentReader) -> None:
        self._store = store

    async def retrieve(self, incident_id: UUID) -> tuple[IncidentDetail, EvidenceItem]:
        incident = await self._store.get_incident(incident_id)
        if incident is None:
            raise IncidentNotFoundError("Incident not found")
        details = {
            "scenario": incident.scenario.value,
            "status": incident.status.value,
            "started_at": incident.started_at.isoformat(),
            "resolved_at": incident.ended_at.isoformat() if incident.ended_at else None,
            "description": incident.description,
        }
        return incident, EvidenceItem(
            evidence_id=make_evidence_id(
                EvidenceSource.INCIDENT,
                EvidenceType.INCIDENT_METADATA,
                str(incident_id),
                details,
            ),
            source=EvidenceSource.INCIDENT,
            evidence_type=EvidenceType.INCIDENT_METADATA,
            timestamp=incident.started_at,
            incident_id=incident_id,
            summary=f"Incident {incident_id} is {incident.status.value}.",
            details=details,
            reference=f"incident:{incident_id}",
        )


class IncidentEventTool:
    def __init__(self, store: IncidentReader, maximum_limit: int) -> None:
        self._store = store
        self._maximum_limit = maximum_limit

    async def retrieve(
        self, incident_id: UUID, limit: int | None = None
    ) -> list[EvidenceItem]:
        incident = await self._store.get_incident(incident_id)
        if incident is None:
            raise IncidentNotFoundError("Incident not found")
        bounded = min(max(limit or self._maximum_limit, 1), self._maximum_limit)
        events = incident.events[-bounded:]
        return [
            EvidenceItem(
                evidence_id=make_evidence_id(
                    EvidenceSource.INCIDENT_EVENT,
                    EvidenceType.INCIDENT_EVENT,
                    str(event.event_id),
                    event.details,
                ),
                source=EvidenceSource.INCIDENT_EVENT,
                evidence_type=EvidenceType.INCIDENT_EVENT,
                timestamp=event.occurred_at,
                incident_id=incident_id,
                summary=f"Incident event recorded: {event.event_type}.",
                details={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "details": sanitize_value(event.details),
                },
                reference=f"incident-event:{event.event_id}",
            )
            for event in events
        ]
