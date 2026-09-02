from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.exceptions import InvalidStateTransition
from app.models import Submission
from app.services.assignment import get_student_state
from app.services.state_machine import transition
from app.services.submission import SubmissionService


def test_invalid_state_transition_is_rejected(db, data):
    state = get_student_state(db, data["assignment"].id, data["student1"].id)
    with pytest.raises(InvalidStateTransition):
        transition(state, "completed", datetime.now(UTC))
    assert state.status == "assigned"


def test_repeated_submission_is_idempotent(db, data):
    service = SubmissionService()
    first = service.submit(
        db, data["student1"], data["assignment"].id, "same-key", text_content="answer"
    )
    second = service.submit(
        db, data["student1"], data["assignment"].id, "same-key", text_content="answer"
    )
    db.flush()
    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(Submission)) == 1
