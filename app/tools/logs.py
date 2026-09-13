import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.config import Settings
from app.evidence import EvidenceItem, EvidenceSource, EvidenceType, make_evidence_id
from app.tools.common import sanitize_value


class ApplicationLogDiagnosticTool:
    """Read only the configured rotating JSONL logs with equality filters."""

    def __init__(self, settings: Settings) -> None:
        self._path = Path(settings.log_path).resolve()
        self._maximum_limit = settings.diagnostic_result_limit
        self._backup_count = settings.log_backup_count

    def retrieve(
        self,
        incident_id: UUID | None = None,
        *,
        event_type: str | None = None,
        level: str | None = None,
        limit: int | None = None,
    ) -> list[EvidenceItem]:
        bounded = min(max(limit or self._maximum_limit, 1), self._maximum_limit)
        records: list[dict[str, object]] = []
        paths = [
            self._path.with_name(f"{self._path.name}.{index}")
            for index in range(self._backup_count, 0, -1)
        ]
        paths.append(self._path)
        for path in paths:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                if incident_id is not None and record.get("incident_id") != str(
                    incident_id
                ):
                    continue
                if event_type is not None and record.get("event_type") != event_type:
                    continue
                if (
                    level is not None
                    and str(record.get("level", "")).upper() != level.upper()
                ):
                    continue
                records.append(record)
        evidence = []
        for index, record in enumerate(records[-bounded:]):
            safe_record = sanitize_value(record)
            timestamp_value = safe_record.get("timestamp")
            try:
                timestamp = datetime.fromisoformat(str(timestamp_value))
            except (TypeError, ValueError):
                timestamp = None
            record_type = str(safe_record.get("event_type", "application_log"))
            evidence_type = (
                EvidenceType.APPLICATION_ERROR
                if record_type == "application_error"
                else EvidenceType.APPLICATION_LOG
            )
            reference = f"application-log:{timestamp_value}:{index}"
            evidence.append(
                EvidenceItem(
                    evidence_id=make_evidence_id(
                        EvidenceSource.APPLICATION_LOG,
                        evidence_type,
                        reference,
                        safe_record,
                    ),
                    source=EvidenceSource.APPLICATION_LOG,
                    evidence_type=evidence_type,
                    timestamp=timestamp,
                    incident_id=incident_id,
                    summary=f"Application log event recorded: {record_type}.",
                    details=safe_record,
                    reference=reference,
                )
            )
        return evidence
