import re
from typing import Any

SENSITIVE_KEY_PARTS = ("password", "secret", "token", "credential", "database_url")
POSTGRES_CREDENTIAL_PATTERN = re.compile(r"(postgres(?:ql)?://)[^/@\s]+@", re.I)


def sanitize_value(value: Any) -> Any:
    """Remove secret-bearing fields while preserving useful diagnostic structure."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS)
                else sanitize_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return POSTGRES_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", value)
    return value


def sanitize_query(value: str | None, maximum: int = 240) -> str | None:
    if value is None:
        return None
    return " ".join(value.split())[:maximum]
