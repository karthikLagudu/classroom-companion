from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Assignment, StudentAssignmentState

FINAL_STATES = {"completed", "cancelled", "submitted"}


def aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)


@dataclass(frozen=True)
class ReminderDecision:
    action: str
    reminder_type: str | None
    reason: str
    defer_until: datetime | None = None


def in_quiet_hours(local_time: datetime, start: int, end: int) -> bool:
    hour = local_time.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def next_permitted_time(now: datetime, timezone_name: str, start: int, end: int) -> datetime:
    local = aware_utc(now).astimezone(ZoneInfo(timezone_name))
    if not in_quiet_hours(local, start, end):
        return aware_utc(now)
    candidate = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if local.hour >= start:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def decide_reminder(
    state: StudentAssignmentState,
    assignment: Assignment,
    now: datetime,
) -> ReminderDecision:
    now = aware_utc(now)
    due = aware_utc(assignment.due_at)
    if assignment.status == "cancelled" or state.status in FINAL_STATES:
        return ReminderDecision("suppress", None, f"Suppressed for {state.status}")
    if state.status == "blocked":
        return ReminderDecision("send", "blocked_support", "Student reported a blocker")
    remaining = due - now
    if remaining.total_seconds() < 0 or state.status == "overdue":
        return ReminderDecision("send", "overdue", "Deadline has passed")
    if remaining <= timedelta(hours=2):
        return ReminderDecision("send", "due_within_2h", "Deadline is within two hours")
    if remaining <= timedelta(hours=24):
        if state.last_activity_at is None:
            return ReminderDecision(
                "send", "silent_due_soon", "No acknowledgement or activity before deadline"
            )
        return ReminderDecision("send", "gentle_due_soon", "Active work is due within 24 hours")
    return ReminderDecision("suppress", None, "Deadline is more than 24 hours away")
