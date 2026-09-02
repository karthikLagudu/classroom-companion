from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.llm.base import LLMProvider
from app.llm.schemas import AssignmentIntent, ProgressIntent

WEEKDAYS = {
    name.lower(): number
    for number, name in enumerate(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    )
}


class DemoLLM(LLMProvider):
    """Deterministic local/demo provider. Production language understanding uses OpenAIProvider."""

    def interpret_assignment(
        self, text: str, timezone_name: str, now: datetime
    ) -> AssignmentIntent:
        clean = " ".join(text.split())
        if len(clean) < 8 or "???" in clean:
            return AssignmentIntent(
                intent="clarify",
                title="Clarification needed",
                instructions=clean or "Missing details",
                due_at=now + timedelta(days=1),
                confidence=0.2,
                clarification_question="What should students do, and when is it due?",
            )
        local_now = now.astimezone(ZoneInfo(timezone_name))
        lower = clean.lower()
        due = local_now + timedelta(days=1)
        due = due.replace(hour=18, minute=0, second=0, microsecond=0)
        if "tomorrow morning" in lower:
            due = due.replace(hour=9)
        elif "tomorrow evening" in lower:
            due = due.replace(hour=18)
        else:
            match = re.search(
                r"(?:(next)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", lower
            )
            if match:
                target = WEEKDAYS[match.group(2)]
                days = (target - local_now.weekday()) % 7
                if days == 0 or match.group(1):
                    days += 7
                due = (local_now + timedelta(days=days)).replace(
                    hour=18, minute=0, second=0, microsecond=0
                )
        tm = re.search(r"(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", lower)
        if tm:
            hour = int(tm.group(1)) % 12 + (12 if tm.group(3) == "pm" else 0)
            due = due.replace(hour=hour, minute=int(tm.group(2) or 0))
        title_source = re.split(
            r"\b(?:due|by|tomorrow|next\s+|monday|tuesday|wednesday|thursday|friday)\b",
            clean,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        title = title_source.strip(" :-,.")[:80] or "Class assignment"
        return AssignmentIntent(
            intent="create_assignment",
            title=title[0].upper() + title[1:],
            instructions=clean,
            due_at=due,
            confidence=0.92,
        )

    def interpret_progress(self, text: str) -> ProgressIntent:
        lower = text.lower()
        percent_match = re.search(r"(\d{1,3})\s*%", lower)
        percent = min(100, int(percent_match.group(1))) if percent_match else None
        if any(word in lower for word in ("blocked", "stuck", "help")):
            status = "blocked"
        elif any(word in lower for word in ("started", "progress", "done", "%")):
            status = "in_progress"
        else:
            status = "acknowledged"
        return ProgressIntent(status=status, message=text, progress_percent=percent, confidence=0.9)

    def summarize_risks(self, facts: list[str]) -> str:
        return "No active risks." if not facts else " ".join(facts)

    def write_reminder(self, reminder_type: str, facts: dict[str, str]) -> str:
        title = facts["title"]
        due = facts["due"]
        if reminder_type == "blocked_support":
            return f"You marked '{title}' as blocked. Your teacher has been alerted. Reply with what would help; it is due {due}."
        if reminder_type == "silent_checkin":
            return f"Quick check-in: we have not heard an update on '{title}', due {due}. Reply with your progress or /blocked if you need help."
        if reminder_type == "due_soon":
            return (
                f"Reminder: '{title}' is due {due}. Submit with /submit or share a progress update."
            )
        return f"'{title}' is overdue. Please submit or tell your teacher what is blocking you."
