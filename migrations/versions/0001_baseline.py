"""Baseline schema and public-demo hardening tables."""

from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).resolve().parents[2] / "db" / "init.sql").read_text(
        encoding="utf-8"
    )
    driver = op.get_bind().connection.driver_connection
    driver.execute(sql, prepare=False)


def downgrade() -> None:
    raise RuntimeError(
        "The portfolio lab baseline migration is intentionally irreversible"
    )
