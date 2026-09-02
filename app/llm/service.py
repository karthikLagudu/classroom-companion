from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.exceptions import LLMInterpretationError
from app.llm.base import LLMProvider
from app.llm.schemas import AssignmentIntent, ProgressIntent
from app.models import LLMInteraction


class LLMService:
    def __init__(self, provider: LLMProvider, minimum_confidence: float = 0.7):
        self.provider = provider
        self.minimum_confidence = minimum_confidence

    def assignment(
        self, db: Session, actor_id: int, text: str, timezone_name: str, now: datetime
    ) -> AssignmentIntent:
        try:
            parsed = self.provider.interpret_assignment(text, timezone_name, now)
            if parsed.intent == "clarify" or parsed.confidence < self.minimum_confidence:
                raise LLMInterpretationError(
                    parsed.clarification_question or "Please clarify the assignment and deadline."
                )
            db.add(
                LLMInteraction(
                    operation="assignment",
                    actor_user_id=actor_id,
                    input_text=text,
                    output_json=parsed.model_dump(mode="json"),
                    confidence=round(parsed.confidence * 100),
                    success=True,
                )
            )
            db.flush()
            return parsed
        except LLMInterpretationError:
            db.add(
                LLMInteraction(
                    operation="assignment",
                    actor_user_id=actor_id,
                    input_text=text,
                    output_json=None,
                    confidence=None,
                    success=False,
                    error="clarification_required",
                )
            )
            db.flush()
            raise
        except Exception as exc:
            db.add(
                LLMInteraction(
                    operation="assignment",
                    actor_user_id=actor_id,
                    input_text=text,
                    output_json=None,
                    confidence=None,
                    success=False,
                    error=type(exc).__name__,
                )
            )
            db.flush()
            raise LLMInterpretationError(
                "I could not understand that safely. Please include the task and an exact or relative deadline."
            ) from exc

    def progress(self, db: Session, actor_id: int, text: str) -> ProgressIntent:
        try:
            parsed = self.provider.interpret_progress(text)
            if parsed.confidence < self.minimum_confidence:
                raise LLMInterpretationError(
                    "Please say whether you started, are blocked, or what percent is done."
                )
            db.add(
                LLMInteraction(
                    operation="progress",
                    actor_user_id=actor_id,
                    input_text=text,
                    output_json=parsed.model_dump(mode="json"),
                    confidence=round(parsed.confidence * 100),
                    success=True,
                )
            )
            db.flush()
            return parsed
        except LLMInterpretationError:
            raise
        except Exception as exc:
            raise LLMInterpretationError("Please clarify your progress update.") from exc

    def risk_summary(self, db: Session, actor_id: int, facts: list[str]) -> str:
        """Summarize authorized facts; a provider failure must not hide the risk list."""
        if not facts:
            return "No students currently need attention."
        try:
            summary = self.provider.summarize_risks(facts)
            db.add(
                LLMInteraction(
                    operation="risk_summary",
                    actor_user_id=actor_id,
                    input_text="\n".join(facts),
                    output_json={"summary": summary},
                    confidence=None,
                    success=True,
                )
            )
            db.flush()
            return summary
        except Exception as exc:  # noqa: BLE001 - provider failures must degrade to factual output
            db.add(
                LLMInteraction(
                    operation="risk_summary",
                    actor_user_id=actor_id,
                    input_text="\n".join(facts),
                    output_json=None,
                    confidence=None,
                    success=False,
                    error=type(exc).__name__,
                )
            )
            db.flush()
            return " ".join(facts)
