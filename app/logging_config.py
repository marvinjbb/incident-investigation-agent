import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import Settings


class JsonFormatter(logging.Formatter):
    """Serialize bounded application events without configuration or credentials."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event_type": getattr(record, "event_type", "application_log"),
            "message": record.getMessage(),
        }
        for field in (
            "incident_id",
            "scenario",
            "path",
            "deployment_version",
            "error_type",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = str(value)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    formatter = JsonFormatter()
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(formatter)
    log_path = Path(settings.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stdout_handler)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)


def log_event(
    logger: logging.Logger, event_type: str, message: str, **fields: Any
) -> None:
    logger.info(message, extra={"event_type": event_type, **fields})
