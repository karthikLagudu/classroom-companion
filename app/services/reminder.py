from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.models import Assignment, Reminder, StudentAssignmentState
from app.reminders.policy import ReminderDecision as PolicyDecision
from app.reminders.policy import decide_reminder as policy_decide_reminder
from app.reminders.processor import ReminderProcessor
from app.services.notification import NotificationService
from app.telegram.client import TelegramClient


def decide_reminder(
    state: StudentAssignmentState, assignment: Assignment, now: datetime
) -> PolicyDecision:
    """Compatibility export for callers; policy now lives in app.reminders.policy."""
    return policy_decide_reminder(state, assignment, now)


class ReminderService:
    """Scoped facade shared by the worker, CLI, and authorized web trigger."""

    def __init__(
        self,
        llm: LLMProvider,
        telegram: TelegramClient,
        settings: Settings | None = None,
    ):
        self.processor = ReminderProcessor(
            settings or get_settings(), llm, NotificationService(telegram)
        )

    def run(
        self,
        db: Session,
        now: datetime | None = None,
        classroom_ids: set[int] | None = None,
    ) -> list[Reminder]:
        return self.processor.run(db, now=now, classroom_ids=classroom_ids)
