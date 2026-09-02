from __future__ import annotations

from datetime import datetime

from app.exceptions import InvalidStateTransition
from app.models import StudentAssignmentState

FINAL_STATES = {"completed", "cancelled"}
TRANSITIONS: dict[str, set[str]] = {
    "assigned": {"acknowledged", "in_progress", "overdue", "cancelled"},
    "acknowledged": {"in_progress", "blocked", "submitted", "overdue", "cancelled"},
    "in_progress": {"blocked", "submitted", "overdue", "cancelled"},
    "blocked": {"in_progress", "submitted", "overdue", "cancelled"},
    "submitted": {"needs_revision", "completed", "cancelled"},
    "needs_revision": {"in_progress", "submitted", "overdue", "cancelled"},
    "overdue": {"in_progress", "blocked", "submitted", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def transition(state: StudentAssignmentState, new_status: str, now: datetime) -> None:
    if new_status == state.status:
        return
    allowed = TRANSITIONS.get(state.status, set())
    if new_status not in allowed:
        raise InvalidStateTransition(f"Cannot transition from {state.status} to {new_status}")
    state.status = new_status
    state.last_activity_at = now
    if new_status == "acknowledged":
        state.acknowledged_at = now
    elif new_status == "in_progress":
        state.started_at = state.started_at or now
        state.block_reason = None
    elif new_status == "blocked":
        state.blocked_at = now
    elif new_status == "submitted":
        state.submitted_at = now
    elif new_status == "completed":
        state.completed_at = now
