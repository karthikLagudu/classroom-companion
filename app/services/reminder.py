from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.base import LLMProvider
from app.models import Assignment, Reminder, StudentAssignmentState, User
from app.telegram.client import TelegramClient

SUPPRESSED_STATES = {"completed", "cancelled", "submitted"}


@dataclass(frozen=True)
class ReminderDecision:
    reminder_type: str | None
    reason: str


def decide_reminder(
    state: StudentAssignmentState, assignment: Assignment, now: datetime
) -> ReminderDecision:
    if assignment.status == "cancelled" or state.status in SUPPRESSED_STATES:
        return ReminderDecision(None, f"Suppressed for {state.status}")
    if state.status == "blocked":
        return ReminderDecision("blocked_support", "Student explicitly reported a blocker")
    last = state.last_activity_at
    if last is None:
        return ReminderDecision("silent_checkin", "No student acknowledgement or progress")
    due = assignment.due_at.replace(tzinfo=assignment.due_at.tzinfo or UTC)
    if due <= now:
        return ReminderDecision("overdue", "Deadline has passed")
    return ReminderDecision("due_soon", "Active work approaching deadline")


class ReminderService:
    def __init__(self, llm: LLMProvider, telegram: TelegramClient):
        self.llm = llm
        self.telegram = telegram

    def run(self, db: Session, now: datetime | None = None) -> list[Reminder]:
        now = now or datetime.now(UTC)
        states = db.execute(
            select(StudentAssignmentState, Assignment, User)
            .join(Assignment, Assignment.id == StudentAssignmentState.assignment_id)
            .join(User, User.id == StudentAssignmentState.student_id)
            .where(Assignment.status == "assigned")
        ).all()
        created: list[Reminder] = []
        day_key = now.date().isoformat()
        for state, assignment, student in states:
            decision = decide_reminder(state, assignment, now)
            if not decision.reminder_type:
                continue
            key = f"policy:{decision.reminder_type}:{assignment.id}:{student.id}:{day_key}"
            existing = db.scalar(select(Reminder).where(Reminder.dedupe_key == key))
            if existing:
                continue
            due = assignment.due_at.replace(tzinfo=assignment.due_at.tzinfo or UTC)
            facts = {
                "student": student.name,
                "title": assignment.title,
                "due": due.astimezone().strftime("%d %b %Y, %I:%M %p %Z"),
            }
            body = self.llm.write_reminder(decision.reminder_type, facts)
            reminder = Reminder(
                assignment_id=assignment.id,
                student_id=student.id,
                reminder_type=decision.reminder_type,
                scheduled_for=now,
                status="pending",
                reason=decision.reason,
                dedupe_key=key,
            )
            db.add(reminder)
            db.flush()
            delivery = self.telegram.send(db, student, None, body, kind="reminder")
            reminder.status = "sent" if delivery.status in {"sent", "logged"} else delivery.status
            reminder.sent_at = now if reminder.status == "sent" else None
            created.append(reminder)
        return created
