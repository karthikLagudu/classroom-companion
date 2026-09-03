from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import ActivityEvent, Assignment, StudentAssignmentState
from app.services.state_machine import transition

ELIGIBLE = {"assigned", "acknowledged", "in_progress", "blocked", "needs_revision"}


class OverdueService:
    def mark_overdue(
        self,
        db: Session,
        assignment: Assignment,
        state: StudentAssignmentState,
        now: datetime,
    ) -> bool:
        due = assignment.due_at.replace(tzinfo=assignment.due_at.tzinfo or UTC)
        now = now.replace(tzinfo=now.tzinfo or UTC)
        if assignment.status == "cancelled" or state.status not in ELIGIBLE or now <= due:
            return False
        previous = state.status
        transition(state, "overdue", now)
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=None,
                event_type="student_assignment_overdue",
                entity_type="student_assignment_state",
                entity_id=state.id,
                metadata_json={"assignment_id": assignment.id, "from": previous},
            )
        )
        return True
