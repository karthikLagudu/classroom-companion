from sqlalchemy import inspect

from app.database import create_database_engine, init_db


def test_init_db_upgrades_legacy_sqlite_schema_without_losing_rows(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    database_engine = create_database_engine(f"sqlite:///{database_path}")

    with database_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE assignments (id INTEGER PRIMARY KEY, title VARCHAR(240))"
        )
        connection.exec_driver_sql("CREATE TABLE feedback (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE notification_deliveries (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql("CREATE TABLE reminders (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "INSERT INTO assignments (id, title) VALUES (1, 'Existing assignment')"
        )

    init_db(database_engine)
    init_db(database_engine)  # A second startup must remain safe.

    schema = inspect(database_engine)
    assert "schedule_version" in {
        column["name"] for column in schema.get_columns("assignments")
    }
    assert "idempotency_key" in {
        column["name"] for column in schema.get_columns("feedback")
    }
    assert {
        "school_id",
        "classroom_id",
        "assignment_id",
        "idempotency_key",
        "attempt_count",
        "last_attempt_at",
        "next_attempt_at",
    }.issubset(
        {column["name"] for column in schema.get_columns("notification_deliveries")}
    )
    assert {"schedule_version", "processed_at"}.issubset(
        {column["name"] for column in schema.get_columns("reminders")}
    )

    with database_engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT title, schedule_version FROM assignments WHERE id = 1"
        ).one()
    assert row == ("Existing assignment", 1)

