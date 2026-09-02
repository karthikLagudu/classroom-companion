from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import ActivityEvent, ProgressEvent, User
from app.services.assignment import get_student_state
from app.services.authorization import require_student_assignment
from app.services.state_machine import transition


class ProgressService:
    def update(
        self,
        db: Session,
        student: User,
        assignment_id: int,
        status: str,
        message: str,
        progress_percent: int | None = None,
    ) -> ProgressEvent:
        assignment = require_student_assignment(db, student, assignment_id)
        state = get_student_state(db, assignment_id, student.id)
        now = datetime.now(UTC)
        transition(state, status, now)
        if status == "blocked":
            state.block_reason = message
        event = ProgressEvent(
            assignment_id=assignment_id,
            student_id=student.id,
            event_type=status,
            message=message,
            progress_percent=progress_percent,
        )
        db.add(event)
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=student.id,
                event_type=f"student_{status}",
                entity_type="assignment",
                entity_id=assignment_id,
                metadata_json={"progress_percent": progress_percent},
            )
        )
        return event
