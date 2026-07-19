"""Goal 2 security remediation schema migration.

The project supports SQLite and PostgreSQL deployments. This migration avoids
table rebuilds so it can add the new invite expiry column safely to either.
Legacy unused invites are expired rather than being silently granted an
indefinite lifetime.
"""
from datetime import datetime

from sqlalchemy import inspect, text

from database.models import IssueReport


MIGRATION_ID = "security_remediation_20260719"


def upgrade(engine) -> None:
    """Apply Goal 2 schema changes without destructively rewriting tables."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "admin_invite" in tables:
        invite_columns = {column["name"] for column in inspector.get_columns("admin_invite")}
        if "expires_at" not in invite_columns:
            timestamp_type = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE admin_invite ADD COLUMN expires_at {timestamp_type}")
                )

        # Fail closed for historical codes that were issued before expiry existed.
        # New invites are always created with an explicit expiry in application code.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE admin_invite "
                    "SET expires_at = :expired_at "
                    "WHERE used_at IS NULL AND expires_at IS NULL"
                ),
                {"expired_at": datetime.utcnow()},
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_admin_invite_unused_expiry "
                    "ON admin_invite (used_at, expires_at)"
                )
            )

    IssueReport.__table__.create(engine, checkfirst=True)
