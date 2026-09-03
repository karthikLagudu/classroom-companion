from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Assignment, ClassMembership, SchoolMembership, StudentAssignmentState, User


@dataclass(frozen=True)
class RiskItem:
    student_id: int
    student_name: str
    assignment_id: int
    assignment_title: str
    risk_score: int
    risk_level: str
    reasons: tuple[str, ...]


class RiskService:
    def for_actor(self, db: Session, actor: User, now: datetime | None = None) -> list[RiskItem]:
        now = now or datetime.now(UTC)
        coordinator_schools = select(SchoolMembership.school_id).where(
            SchoolMembership.user_id == actor.id,
            SchoolMembership.role == "coordinator",
        )
        teacher_classes = select(ClassMembership.classroom_id).where(
            ClassMembership.user_id == actor.id,
            ClassMembership.role == "teacher",
        )
        rows = db.execute(
            select(StudentAssignmentState, User, Assignment)
            .join(User, User.id == StudentAssignmentState.student_id)
            .join(Assignment, Assignment.id == StudentAssignmentState.assignment_id)
            .where(
                Assignment.status == "assigned",
                or_(
                    Assignment.school_id.in_(coordinator_schools),
                    Assignment.classroom_id.in_(teacher_classes),
                ),
            )
        ).all()
        result: list[RiskItem] = []
        for state, student, assignment in rows:
            if state.status in {"submitted", "completed", "cancelled"}:
                continue
            score = 0
            reasons: list[str] = []
            due = assignment.due_at.replace(tzinfo=assignment.due_at.tzinfo or UTC)
            if state.status == "blocked":
                score += 70
                reasons.append("blocked")
            if state.status == "overdue" or due < now:
                score += 80
                reasons.append("overdue")
            elif due <= now + timedelta(hours=24):
                score += 30
                reasons.append("due within 24 hours")
            if state.last_activity_at is None:
                score += 25
                reasons.append("no acknowledgement")
            else:
                last = state.last_activity_at.replace(tzinfo=state.last_activity_at.tzinfo or UTC)
                if last < now - timedelta(days=2):
                    score += 20
                    reasons.append("inactive for two days")
                elif last >= now - timedelta(hours=2):
                    score -= 10
            score = max(0, min(100, score))
            if score < 25:
                continue
            level = "critical" if score >= 80 else "high" if score >= 60 else "medium"
            result.append(
                RiskItem(
                    student_id=student.id,
                    student_name=student.name,
                    assignment_id=assignment.id,
                    assignment_title=assignment.title,
                    risk_score=score,
                    risk_level=level,
                    reasons=tuple(reasons),
                )
            )
        return sorted(result, key=lambda item: (-item.risk_score, item.student_name))
