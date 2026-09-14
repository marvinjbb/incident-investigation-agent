from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from backend environment variables."""

    app_name: str = "Incident Investigation Agent"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "incident_lab"
    postgres_user: str = "incident_app"
    postgres_password: SecretStr = SecretStr("incident_lab_dev")
    database_connect_timeout_seconds: int = 3
    database_pool_size: int = 3
    database_pool_timeout_seconds: float = 1.0
    incident_default_duration_seconds: int = 8
    incident_max_duration_seconds: int = 120
    incident_history_limit: int = 100
    deployment_history_limit: int = 100
    diagnostic_result_limit: int = 25
    evidence_result_limit: int = 100
    log_path: str = "logs/application.jsonl"
    log_max_bytes: int = 1_000_000
    log_backup_count: int = 2
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-luna"
    investigation_max_iterations: int = 6
    investigation_max_tool_calls: int = 10
    investigation_timeout_seconds: float = 45.0
    investigation_max_output_tokens: int = 3000
    remediation_proposal_ttl_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def database_connection_kwargs(self) -> dict[str, str | int]:
        return {
            "host": self.postgres_host,
            "port": self.postgres_port,
            "dbname": self.postgres_db,
            "user": self.postgres_user,
            "password": self.postgres_password.get_secret_value(),
            "connect_timeout": self.database_connect_timeout_seconds,
        }

    @property
    def has_openai_key(self) -> bool:
        return bool(
            self.openai_api_key and self.openai_api_key.get_secret_value().strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
