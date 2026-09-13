from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.evidence import EvidenceItem, EvidenceSource, EvidenceType
from app.models import IncidentDetail, IncidentStatus, ScenarioType
from app.tools.collector import IncidentEvidenceCollector


def item(incident_id, source, evidence_type, name) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"ev_{name}",
        source=source,
        evidence_type=evidence_type,
        incident_id=incident_id,
        summary=name,
    )


class Metadata:
    def __init__(self, incident) -> None:
        self.incident = incident

    async def retrieve(self, incident_id):
        return self.incident, item(
            incident_id,
            EvidenceSource.INCIDENT,
            EvidenceType.INCIDENT_METADATA,
            "metadata",
        )


class Events:
    async def retrieve(self, incident_id):
        return [
            item(
                incident_id,
                EvidenceSource.INCIDENT_EVENT,
                EvidenceType.INCIDENT_EVENT,
                "event",
            )
        ]


class Postgres:
    async def blocking_relationships(self, incident_id):
        return []

    async def connection_utilization(self, incident_id):
        raise RuntimeError("optional source unavailable")


class Pool:
    def retrieve(self, incident_id):
        return item(
            incident_id,
            EvidenceSource.CONNECTION_POOL,
            EvidenceType.POOL_STATE,
            "pool",
        )


class Deployments:
    async def retrieve(self, incident_id):
        return []


class Logs:
    def retrieve(self, incident_id):
        return []


class Runbooks:
    def retrieve(self, incident_id, scenario):
        return item(
            incident_id,
            EvidenceSource.RUNBOOK,
            EvidenceType.TROUBLESHOOTING_GUIDANCE,
            "runbook",
        )


@pytest.mark.asyncio
async def test_collector_aggregates_without_reasoning_and_tolerates_empty_sources():
    incident_id = uuid4()
    incident = IncidentDetail(
        incident_id=incident_id,
        scenario=ScenarioType.CONNECTION_EXHAUSTION,
        status=IncidentStatus.ACTIVE,
        started_at=datetime.now(UTC),
        description="test",
    )
    collector = IncidentEvidenceCollector(
        Metadata(incident),  # type: ignore[arg-type]
        Events(),  # type: ignore[arg-type]
        Postgres(),  # type: ignore[arg-type]
        Pool(),  # type: ignore[arg-type]
        Deployments(),  # type: ignore[arg-type]
        Logs(),  # type: ignore[arg-type]
        Runbooks(),  # type: ignore[arg-type]
        maximum_items=3,
    )

    bundle = await collector.collect(incident_id)

    assert bundle.scenario == "connection_exhaustion"
    assert [evidence.evidence_id for evidence in bundle.items] == [
        "ev_metadata",
        "ev_event",
        "ev_pool",
    ]
