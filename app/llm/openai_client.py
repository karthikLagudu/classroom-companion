from __future__ import annotations

import json
from datetime import datetime

from openai import OpenAI

from app.llm.base import LLMProvider
from app.llm.schemas import (
    AssignmentIntent,
    ProgressIntent,
    RiskSummary,
    StudentIntent,
    TeacherIntent,
)


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key, timeout=20, max_retries=2)
        self.model = model

    def _structured(self, system: str, user: str, schema: type):
        response = self.client.responses.parse(
            model=self.model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text_format=schema,
        )
        if not response.output_parsed:
            raise ValueError("Model returned no validated output")
        return response.output_parsed

    def interpret_assignment(
        self, text: str, timezone_name: str, now: datetime
    ) -> AssignmentIntent:
        system = (
            "Extract a classroom assignment. Resolve relative dates using the supplied current time and timezone. "
            "Use clarify when core work or deadline is ambiguous. Do not invent class/user/database identifiers."
        )
        return self._structured(
            system,
            f"Now: {now.isoformat()}\nTimezone: {timezone_name}\nMessage: {text}",
            AssignmentIntent,
        )

    def interpret_progress(self, text: str) -> ProgressIntent:
        return self._structured(
            "Interpret a student's assignment update as acknowledged, in_progress, or blocked. Preserve their meaning.",
            text,
            ProgressIntent,
        )

    def interpret_teacher(
        self, text: str, timezone_name: str, now: datetime
    ) -> TeacherIntent:
        return self._structured(
            "Classify the teacher's message into the supplied schema. Extract human references only, never IDs unless explicitly written. Resolve relative deadlines from the provided aware current time and IANA timezone. Set requires_clarification when the action, assignment, class, or deadline is unsafe to infer.",
            f"Now: {now.isoformat()}\nTimezone: {timezone_name}\nMessage: {text}",
            TeacherIntent,
        )

    def interpret_student(self, text: str) -> StudentIntent:
        return self._structured(
            "Classify a student's informal classroom message. Preserve blocker meaning and extract progress percentage. Use submit_text only when work content is actually supplied or the student clearly announces an imminent attachment. Never choose a database ID.",
            text,
            StudentIntent,
        )

    def summarize_risks(self, facts: list[str]) -> str:
        result = self._structured(
            "Write a concise factual classroom risk summary. Do not infer facts not supplied.",
            "\n".join(facts) or "No risks supplied.",
            RiskSummary,
        )
        return result.summary

    def write_reminder(self, reminder_type: str, facts: dict[str, str]) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions="Write one short supportive classroom reminder using only supplied facts. Never shame the student.",
            input=json.dumps({"type": reminder_type, **facts}),
        )
        return response.output_text.strip()
