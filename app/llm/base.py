from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.llm.schemas import AssignmentIntent, ProgressIntent


class LLMProvider(ABC):
    @abstractmethod
    def interpret_assignment(
        self, text: str, timezone_name: str, now: datetime
    ) -> AssignmentIntent:
        raise NotImplementedError  # abstract contract; both real and demo implementations exist

    @abstractmethod
    def interpret_progress(self, text: str) -> ProgressIntent:
        raise NotImplementedError

    @abstractmethod
    def summarize_risks(self, facts: list[str]) -> str:
        raise NotImplementedError

    @abstractmethod
    def write_reminder(self, reminder_type: str, facts: dict[str, str]) -> str:
        raise NotImplementedError
