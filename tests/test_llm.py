from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.exceptions import LLMInterpretationError
from app.llm.base import LLMProvider
from app.llm.schemas import AssignmentIntent
from app.llm.service import LLMService
from app.models import Assignment, LLMInteraction


class BrokenProvider(LLMProvider):
    def interpret_assignment(self, text, timezone_name, now):
        raise ValueError("malformed JSON")

    def interpret_progress(self, text):
        raise ValueError("broken")

    def summarize_risks(self, facts):
        return ""

    def write_reminder(self, reminder_type, facts):
        return "reminder"


class LowConfidenceProvider(BrokenProvider):
    def interpret_assignment(self, text, timezone_name, now):
        return AssignmentIntent(
            intent="create_assignment",
            title="A title",
            instructions="Do work",
            due_at=now + timedelta(days=1),
            confidence=0.2,
        )


@pytest.mark.parametrize("provider", [BrokenProvider(), LowConfidenceProvider()])
def test_invalid_or_low_confidence_llm_causes_no_assignment_mutation(db, data, provider):
    before = db.scalar(select(func.count()).select_from(Assignment))
    with pytest.raises(LLMInterpretationError):
        LLMService(provider).assignment(
            db, data["teacher1"].id, "unclear", "Asia/Kolkata", datetime.now(UTC)
        )
    db.flush()
    assert db.scalar(select(func.count()).select_from(Assignment)) == before


def test_student_intent_failure_is_logged(db, data):
    with pytest.raises(LLMInterpretationError):
        LLMService(BrokenProvider()).student_intent(db, data["student1"].id, "unclear")
    row = db.scalar(
        select(LLMInteraction).where(LLMInteraction.operation == "student_intent")
    )
    assert row and row.success is False and row.error == "NotImplementedError"
