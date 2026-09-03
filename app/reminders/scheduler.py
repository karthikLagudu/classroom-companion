from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, AssignmentTarget, Reminder


class ReminderScheduler:
    SCHEDULES = (("deadline_24h", timedelta(hours=24)), ("deadline_2h", timedelta(hours=2)))

    def schedule_assignment(
        self, db: Session, assignment: Assignment, now: datetime | None = None
    ) -> list[Reminder]:
        now = now or datetime.now(UTC)
        due = assignment.due_at.replace(tzinfo=assignment.due_at.tzinfo or UTC)
        student_ids = list(
            db.scalars(
                select(AssignmentTarget.student_id).where(
                    AssignmentTarget.assignment_id == assignment.id
                )
            )
        )
        rows: list[Reminder] = []
        for student_id in student_ids:
            for reminder_type, offset in self.SCHEDULES:
                key = (
                    f"assignment:{assignment.id}:student:{student_id}:type:{reminder_type}:"
                    f"version:{assignment.schedule_version}"
                )
                existing = db.scalar(select(Reminder).where(Reminder.dedupe_key == key))
                if existing:
                    rows.append(existing)
                    continue
                row = Reminder(
                    assignment_id=assignment.id,
                    student_id=student_id,
                    reminder_type=reminder_type,
                    scheduled_for=max(now, due - offset),
                    status="pending",
                    reason=f"Persistent {int(offset.total_seconds() // 3600)}-hour deadline job",
                    dedupe_key=key,
                    schedule_version=assignment.schedule_version,
                )
                db.add(row)
                rows.append(row)
        return rows

    def reschedule(
        self, db: Session, assignment: Assignment, now: datetime | None = None
    ) -> list[Reminder]:
        for row in db.scalars(
            select(Reminder).where(
                Reminder.assignment_id == assignment.id,
                Reminder.status.in_(["pending", "deferred"]),
            )
        ):
            row.status = "cancelled"
            row.reason = "Superseded by deadline update"
            row.processed_at = now or datetime.now(UTC)
        return self.schedule_assignment(db, assignment, now)

    def cancel(self, db: Session, assignment_id: int, reason: str) -> None:
        now = datetime.now(UTC)
        for row in db.scalars(
            select(Reminder).where(
                Reminder.assignment_id == assignment_id,
                Reminder.status.in_(["pending", "deferred"]),
            )
        ):
            row.status = "cancelled"
            row.reason = reason
            row.processed_at = now
