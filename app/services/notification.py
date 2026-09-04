from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, AssignmentTarget, Classroom, NotificationDelivery, User
from app.telegram.client import TelegramClient


class NotificationService:
    """Canonical assignment notification boundary for web, Telegram, and workers."""

    def __init__(self, telegram: TelegramClient):
        self.telegram = telegram

    @staticmethod
    def format_deadline(value: datetime, timezone_name: str) -> str:
        aware = value.replace(tzinfo=value.tzinfo or UTC)
        return aware.astimezone(ZoneInfo(timezone_name)).strftime(
            "%A, %d %B %Y at %I:%M %p %Z"
        )

    def students(self, db: Session, assignment: Assignment) -> list[User]:
        return list(
            db.scalars(
                select(User)
                .join(AssignmentTarget, AssignmentTarget.student_id == User.id)
                .where(AssignmentTarget.assignment_id == assignment.id)
            )
        )

    def _send(
        self,
        db: Session,
        assignment: Assignment,
        student: User,
        kind: str,
        body: str,
        key: str,
        *,
        buttons: bool = False,
    ) -> NotificationDelivery:
        markup = None
        if buttons:
            markup = {
                "inline_keyboard": [
                    [
                        {"text": "Acknowledge", "callback_data": f"ack:{assignment.id}"},
                        {"text": "I'm blocked", "callback_data": f"blocked:{assignment.id}"},
                    ],
                    [{"text": "Submit / Help", "callback_data": f"help:{assignment.id}"}],
                ]
            }
        return self.telegram.send(
            db,
            student,
            None,
            body,
            kind=kind,
            reply_markup=markup,
            school_id=assignment.school_id,
            classroom_id=assignment.classroom_id,
            assignment_id=assignment.id,
            idempotency_key=key,
        )

    def notify_assignment_created(
        self, db: Session, assignment: Assignment
    ) -> list[NotificationDelivery]:
        classroom = db.get(Classroom, assignment.classroom_id)
        body = (
            "📚 New Homework Assigned\n\n"
            f"Assignment:\n{assignment.title}\n\n"
            f"📝 Instructions:\n{assignment.instructions}\n\n"
            f"⏰ Due:\n{self.format_deadline(assignment.due_at, assignment.timezone)}\n\n"
            f"Class:\n{classroom.name if classroom else f'Class {assignment.classroom_id}'}"
        )
        deliveries = []
        for student in self.students(db, assignment):
            deliveries.append(self._send(
                db,
                assignment,
                student,
                "assignment_created",
                body,
                f"assignment-created:{assignment.id}:{student.id}",
                buttons=True,
            ))
            if student.telegram_chat_id:
                from app.telegram.conversation import ConversationService

                ConversationService(
                    self.telegram.settings.conversation_context_minutes
                ).set(
                    db,
                    student,
                    student.telegram_chat_id,
                    assignment_id=assignment.id,
                )
        return deliveries

    def notify_deadline_changed(
        self, db: Session, assignment: Assignment, old_deadline: datetime
    ) -> list[NotificationDelivery]:
        body = (
            f"Deadline updated for {assignment.title}:\n"
            f"Old: {self.format_deadline(old_deadline, assignment.timezone)}\n"
            f"New: {self.format_deadline(assignment.due_at, assignment.timezone)}"
        )
        return [
            self._send(
                db,
                assignment,
                student,
                "deadline_updated",
                body,
                f"deadline:{assignment.id}:v{assignment.schedule_version}:{student.id}",
            )
            for student in self.students(db, assignment)
        ]

    def notify_instructions_changed(
        self, db: Session, assignment: Assignment, operation_key: str
    ) -> list[NotificationDelivery]:
        body = f"Your teacher clarified {assignment.title}:\n{assignment.instructions}"
        return [
            self._send(
                db,
                assignment,
                student,
                "instructions_updated",
                body,
                f"instructions:{operation_key}:{student.id}",
            )
            for student in self.students(db, assignment)
        ]

    def notify_assignment_cancelled(
        self, db: Session, assignment: Assignment
    ) -> list[NotificationDelivery]:
        body = f"Assignment cancelled: {assignment.title}. No further action is required."
        return [
            self._send(
                db,
                assignment,
                student,
                "assignment_cancelled",
                body,
                f"cancelled:{assignment.id}:{student.id}",
            )
            for student in self.students(db, assignment)
        ]

    def notify_feedback(
        self, db: Session, assignment: Assignment, student: User, message: str, key: str
    ) -> NotificationDelivery:
        return self._send(
            db,
            assignment,
            student,
            "feedback",
            f"Teacher feedback for {assignment.title}:\n{message}",
            key,
        )

    def notify_reminder(
        self,
        db: Session,
        assignment: Assignment,
        student: User,
        reminder_type: str,
        body: str,
        key: str,
    ) -> NotificationDelivery:
        return self._send(db, assignment, student, reminder_type, body, key)
