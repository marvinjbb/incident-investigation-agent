import json
import logging
from datetime import UTC, datetime
from typing import Any


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


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_event(
    logger: logging.Logger, event_type: str, message: str, **fields: Any
) -> None:
    logger.info(message, extra={"event_type": event_type, **fields})
