from pathlib import Path
from uuid import UUID

from app.evidence import EvidenceItem, EvidenceSource, EvidenceType, make_evidence_id
from app.models import ScenarioType

RUNBOOK_FILES = {
    ScenarioType.BLOCKED_QUERY: "blocked-query.md",
    ScenarioType.CONNECTION_EXHAUSTION: "connection-exhaustion.md",
    ScenarioType.BAD_DEPLOYMENT: "bad-deployment.md",
}


class RunbookNotAllowedError(ValueError):
    pass


class RunbookDiagnosticTool:
    """Retrieve only scenario-mapped runbooks from the application-owned directory."""

    def __init__(self, runbook_directory: Path | None = None) -> None:
        self._directory = (
            runbook_directory or Path(__file__).resolve().parents[2] / "runbooks"
        ).resolve()

    def retrieve(self, incident_id: UUID, scenario: ScenarioType) -> EvidenceItem:
        try:
            filename = RUNBOOK_FILES[scenario]
        except KeyError as exc:
            raise RunbookNotAllowedError("Runbook is not allowlisted") from exc
        path = (self._directory / filename).resolve()
        if path.parent != self._directory or not path.is_file():
            raise RuntimeError("Approved runbook is unavailable")
        content = path.read_text(encoding="utf-8")
        details = {"scenario": scenario.value, "content": content}
        reference = f"runbook:{filename}"
        return EvidenceItem(
            evidence_id=make_evidence_id(
                EvidenceSource.RUNBOOK,
                EvidenceType.TROUBLESHOOTING_GUIDANCE,
                reference,
                details,
            ),
            source=EvidenceSource.RUNBOOK,
            evidence_type=EvidenceType.TROUBLESHOOTING_GUIDANCE,
            incident_id=incident_id,
            summary=f"Approved {scenario.value} troubleshooting runbook retrieved.",
            details=details,
            reference=reference,
        )
