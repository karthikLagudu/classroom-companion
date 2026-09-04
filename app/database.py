from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


# The take-home project intentionally uses SQLAlchemy ``create_all`` instead of
# Alembic. ``create_all`` creates new tables, but it cannot add columns to an
# existing SQLite database. Keep these upgrades additive and idempotent so a
# developer can pull a newer version without deleting their local data.
_SQLITE_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "assignments": {
        "schedule_version": "INTEGER NOT NULL DEFAULT 1",
    },
    "feedback": {
        "idempotency_key": "VARCHAR(160)",
    },
    "notification_deliveries": {
        "school_id": "INTEGER REFERENCES schools(id) ON DELETE CASCADE",
        "classroom_id": "INTEGER REFERENCES classrooms(id) ON DELETE CASCADE",
        "assignment_id": "INTEGER REFERENCES assignments(id) ON DELETE CASCADE",
        "idempotency_key": "VARCHAR(180)",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "last_attempt_at": "DATETIME",
        "next_attempt_at": "DATETIME",
    },
    "reminders": {
        "schedule_version": "INTEGER NOT NULL DEFAULT 1",
        "processed_at": "DATETIME",
    },
}

_SQLITE_ADDITIVE_INDEXES = (
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_feedback_idempotency_key "
        "ON feedback (idempotency_key)"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_delivery_idempotency_key "
        "ON notification_deliveries (idempotency_key)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_school_id "
        "ON notification_deliveries (school_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_classroom_id "
        "ON notification_deliveries (classroom_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_assignment_id "
        "ON notification_deliveries (assignment_id)"
    ),
)


class Base(DeclarativeBase):
    pass


def create_database_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


engine = create_database_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def upgrade_sqlite_schema(database_engine: Engine) -> None:
    """Apply safe, additive upgrades needed by pre-existing demo databases."""
    if database_engine.dialect.name != "sqlite":
        return

    inspector = inspect(database_engine)
    table_names = set(inspector.get_table_names())
    existing_columns = {
        table_name: {column["name"] for column in inspector.get_columns(table_name)}
        for table_name in _SQLITE_ADDITIVE_COLUMNS
        if table_name in table_names
    }

    upgraded: list[str] = []
    with database_engine.begin() as connection:
        for table_name, columns in _SQLITE_ADDITIVE_COLUMNS.items():
            if table_name not in existing_columns:
                continue
            for column_name, column_ddl in columns.items():
                if column_name in existing_columns[table_name]:
                    continue
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_ddl}'
                )
                upgraded.append(f"{table_name}.{column_name}")

        for index_ddl in _SQLITE_ADDITIVE_INDEXES:
            connection.exec_driver_sql(index_ddl)

    if upgraded:
        logger.info("Applied SQLite schema upgrades: %s", ", ".join(upgraded))


def init_db(database_engine: Engine = engine) -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(database_engine)
    upgrade_sqlite_schema(database_engine)
