from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.base import LLMProvider
from app.models import Assignment, LLMInteraction, Reminder, School, StudentAssignmentState, User
from app.reminders.policy import decide_reminder, in_quiet_hours, next_permitted_time
from app.services.notification import NotificationService
from app.services.overdue import OverdueService

logger = logging.getLogger(__name__)


class ReminderProcessor:
    def __init__(
        self,
        settings: Settings,
        llm: LLMProvider,
        notifications: NotificationService,
    ):
        self.settings = settings
        self.llm = llm
        self.notifications = notifications
        self.overdue = OverdueService()

    def run(
        self,
        db: Session,
        now: datetime | None = None,
        classroom_ids: set[int] | None = None,
    ) -> list[Reminder]:
        current = now or datetime.now(UTC)
        now = current.replace(tzinfo=current.tzinfo or UTC)
        query = (
            select(Reminder, Assignment, StudentAssignmentState, User, School)
            .join(Assignment, Assignment.id == Reminder.assignment_id)
            .join(
                StudentAssignmentState,
                (StudentAssignmentState.assignment_id == Reminder.assignment_id)
                & (StudentAssignmentState.student_id == Reminder.student_id),
            )
            .join(User, User.id == Reminder.student_id)
            .join(School, School.id == Assignment.school_id)
            .where(
                Reminder.status.in_(["pending", "deferred"]),
                Reminder.scheduled_for <= now,
            )
            .order_by(Reminder.scheduled_for, Reminder.id)
        )
        if classroom_ids is not None:
            if not classroom_ids:
                return []
            query = query.where(Assignment.classroom_id.in_(classroom_ids))
        processed: list[Reminder] = []
        for reminder, assignment, state, student, school in db.execute(query):
            if reminder.schedule_version != assignment.schedule_version:
                reminder.status = "cancelled"
                reminder.reason = "Stale schedule version"
                reminder.processed_at = now
                processed.append(reminder)
                continue
            self.overdue.mark_overdue(db, assignment, state, now)
            local_now = now.astimezone(ZoneInfo(school.timezone))
            if in_quiet_hours(
                local_now, self.settings.quiet_hour_start, self.settings.quiet_hour_end
            ):
                reminder.status = "deferred"
                reminder.reason = "Deferred for school quiet hours"
                reminder.scheduled_for = next_permitted_time(
                    now,
                    school.timezone,
                    self.settings.quiet_hour_start,
                    self.settings.quiet_hour_end,
                )
                processed.append(reminder)
                logger.info(
                    "reminder_deferred assignment_id=%s student_id=%s until=%s",
                    assignment.id,
                    student.id,
                    reminder.scheduled_for,
                )
                continue
            decision = decide_reminder(state, assignment, now)
            if decision.action == "suppress":
                reminder.status = "suppressed"
                reminder.reason = decision.reason
                reminder.processed_at = now
                processed.append(reminder)
                continue
            if decision.reminder_type == "blocked_support":
                recent = db.scalar(
                    select(Reminder.id).where(
                        Reminder.assignment_id == assignment.id,
                        Reminder.student_id == student.id,
                        Reminder.reminder_type == "blocked_support",
                        Reminder.sent_at >= now - timedelta(hours=12),
                    )
                )
                if recent:
                    reminder.status = "suppressed"
                    reminder.reason = "Blocked follow-up frequency limit"
                    reminder.processed_at = now
                    processed.append(reminder)
                    continue
            facts = {
                "student": student.name,
                "title": assignment.title,
                "due": assignment.due_at.replace(
                    tzinfo=assignment.due_at.tzinfo or UTC
                ).astimezone(ZoneInfo(school.timezone)).strftime("%d %b %Y, %I:%M %p %Z"),
            }
            try:
                body = self.llm.write_reminder(str(decision.reminder_type), facts)
                db.add(
                    LLMInteraction(
                        operation="reminder_wording",
                        actor_user_id=None,
                        input_text=str({"type": decision.reminder_type, **facts}),
                        output_json={"body": body},
                        confidence=None,
                        success=True,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - provider failures use deterministic fallback
                body = f"Reminder: {assignment.title} is due {facts['due']}. Reply with progress or ask for help."
                db.add(
                    LLMInteraction(
                        operation="reminder_wording",
                        actor_user_id=None,
                        input_text=str({"type": decision.reminder_type, **facts}),
                        output_json=None,
                        confidence=None,
                        success=False,
                        error=type(exc).__name__,
                    )
                )
            delivery = self.notifications.notify_reminder(
                db,
                assignment,
                student,
                str(decision.reminder_type),
                body,
                f"reminder:{reminder.dedupe_key}",
            )
            reminder.reminder_type = str(decision.reminder_type)
            reminder.reason = decision.reason
            reminder.processed_at = now
            reminder.status = "sent" if delivery.status in {"sent", "logged"} else delivery.status
            reminder.sent_at = now if reminder.status == "sent" else None
            processed.append(reminder)
        return processed
