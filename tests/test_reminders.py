from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Reminder
from app.services.assignment import AssignmentService, get_student_state
from app.services.reminder import decide_reminder


def test_blocked_and_silent_have_different_decisions(db, data):
    now = datetime.now(UTC)
    data["assignment"].due_at = now + timedelta(hours=20)
    blocked = get_student_state(db, data["assignment"].id, data["student1"].id)
    silent = get_student_state(db, data["assignment"].id, data["student2"].id)
    blocked.status = "blocked"
    blocked.block_reason = "No laptop"
    blocked.last_activity_at = now
    assert decide_reminder(blocked, data["assignment"], now).reminder_type == "blocked_support"
    assert decide_reminder(silent, data["assignment"], now).reminder_type == "silent_due_soon"


def test_completed_assignment_suppresses_normal_reminder(db, data):
    state = get_student_state(db, data["assignment"].id, data["student1"].id)
    state.status = "completed"
    decision = decide_reminder(state, data["assignment"], datetime.now(UTC))
    assert decision.reminder_type is None


def test_runner_creates_different_reminders(app, db, data):
    from app.reminders.scheduler import ReminderScheduler

    now = datetime.now(UTC).replace(hour=6, minute=30, second=0, microsecond=0) + timedelta(
        days=1
    )
    data["assignment"].due_at = now + timedelta(hours=20)
    ReminderScheduler().schedule_assignment(db, data["assignment"], now)
    state = get_student_state(db, data["assignment"].id, data["student1"].id)
    state.status = "blocked"
    state.block_reason = "Need help"
    state.last_activity_at = now
    reminders = app.state.reminder_service.run(db, now=now)
    assert {item.reminder_type for item in reminders} == {"blocked_support", "silent_due_soon"}


def test_deadline_update_cancels_and_reschedules_pending_reminders(db, data):
    old = Reminder(
        assignment_id=data["assignment"].id,
        student_id=data["student1"].id,
        reminder_type="due_soon",
        scheduled_for=datetime.now(UTC),
        status="pending",
        reason="old",
        dedupe_key="old-reminder",
    )
    db.add(old)
    db.flush()
    new_due = datetime.now(UTC) + timedelta(days=5)
    AssignmentService().update_deadline(db, data["teacher1"], data["assignment"].id, new_due)
    db.flush()
    assert old.status == "cancelled"
    pending = list(
        db.scalars(
            select(Reminder).where(
                Reminder.assignment_id == data["assignment"].id, Reminder.status == "pending"
            )
        )
    )
    assert len(pending) == 4
    assert all(item.dedupe_key != "old-reminder" for item in pending)
