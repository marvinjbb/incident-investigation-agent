from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.evidence import EvidenceItem, EvidenceSource, EvidenceType, make_evidence_id
from app.models import Deployment


class DeploymentReader(Protocol):
    async def active_version(self) -> str: ...
    async def list_deployments(self) -> list[Deployment]: ...


class DeploymentDiagnosticTool:
    def __init__(self, store: DeploymentReader, maximum_limit: int) -> None:
        self._store = store
        self._maximum_limit = maximum_limit

    async def retrieve(
        self, incident_id: UUID, limit: int | None = None
    ) -> list[EvidenceItem]:
        bounded = min(max(limit or self._maximum_limit, 1), self._maximum_limit)
        active_version = await self._store.active_version()
        deployments = (await self._store.list_deployments())[:bounded]
        evidence = []
        for deployment in deployments:
            details = {
                "deployment_id": str(deployment.deployment_id),
                "version": deployment.version,
                "status": deployment.status,
                "became_active": deployment.became_active,
                "is_currently_active": deployment.version == active_version,
            }
            reference = f"deployment:{deployment.deployment_id}"
            evidence.append(
                EvidenceItem(
                    evidence_id=make_evidence_id(
                        EvidenceSource.DEPLOYMENT,
                        EvidenceType.DEPLOYMENT_CHANGE,
                        reference,
                        details,
                    ),
                    source=EvidenceSource.DEPLOYMENT,
                    evidence_type=EvidenceType.DEPLOYMENT_CHANGE,
                    timestamp=deployment.deployed_at,
                    incident_id=incident_id,
                    summary=(
                        f"Deployment {deployment.version} recorded with "
                        f"status {deployment.status}."
                    ),
                    details=details,
                    reference=reference,
                )
            )
        if not deployments:
            details = {"active_version": active_version, "history_available": False}
            evidence.append(
                EvidenceItem(
                    evidence_id=make_evidence_id(
                        EvidenceSource.DEPLOYMENT,
                        EvidenceType.DEPLOYMENT_CHANGE,
                        "deployment:active",
                        details,
                    ),
                    source=EvidenceSource.DEPLOYMENT,
                    evidence_type=EvidenceType.DEPLOYMENT_CHANGE,
                    timestamp=datetime.now(UTC),
                    incident_id=incident_id,
                    summary=f"The active application version is {active_version}.",
                    details=details,
                    reference="deployment:active",
                )
            )
        return evidence
