from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models import (
    ActivityEvent,
    Assignment,
    AssignmentTarget,
    ClassMembership,
    IdempotencyKey,
    Reminder,
    StudentAssignmentState,
    User,
)
from app.services.authorization import require_teacher_class
from app.services.state_machine import transition


class AssignmentService:
    def create(
        self,
        db: Session,
        actor: User,
        classroom_id: int,
        title: str,
        instructions: str,
        due_at: datetime,
        timezone_name: str,
        idempotency_key: str | None = None,
    ) -> Assignment:
        classroom = require_teacher_class(db, actor, classroom_id)
        if due_at.tzinfo is None:
            raise ValidationError("Deadline must include a timezone")
        if due_at <= datetime.now(UTC):
            raise ValidationError("Deadline must be in the future")
        key = idempotency_key or f"assignment:{uuid.uuid4()}"
        existing_key = db.get(IdempotencyKey, key)
        if existing_key and existing_key.result_reference:
            existing = db.get(Assignment, int(existing_key.result_reference))
            if existing:
                return existing
        students = list(
            db.scalars(
                select(User)
                .join(ClassMembership, ClassMembership.user_id == User.id)
                .where(
                    ClassMembership.classroom_id == classroom_id, ClassMembership.role == "student"
                )
            )
        )
        if not students:
            raise ValidationError("Class has no students")
        assignment = Assignment(
            school_id=classroom.school_id,
            classroom_id=classroom_id,
            created_by_user_id=actor.id,
            title=title.strip(),
            instructions=instructions.strip(),
            status="assigned",
            due_at=due_at,
            timezone=timezone_name,
        )
        db.add(assignment)
        db.flush()
        for student in students:
            db.add(AssignmentTarget(assignment_id=assignment.id, student_id=student.id))
            db.add(
                StudentAssignmentState(
                    assignment_id=assignment.id, student_id=student.id, status="assigned"
                )
            )
            self._schedule_due_reminder(db, assignment, student.id)
        db.add(
            ActivityEvent(
                school_id=classroom.school_id,
                classroom_id=classroom_id,
                actor_user_id=actor.id,
                event_type="assignment_created",
                entity_type="assignment",
                entity_id=assignment.id,
                metadata_json={"title": assignment.title},
            )
        )
        db.add(
            IdempotencyKey(
                key=key, operation="create_assignment", result_reference=str(assignment.id)
            )
        )
        db.flush()
        return assignment

    def _schedule_due_reminder(self, db: Session, assignment: Assignment, student_id: int) -> None:
        scheduled = max(datetime.now(UTC), assignment.due_at - timedelta(hours=24))
        stamp = assignment.due_at.isoformat()
        db.add(
            Reminder(
                assignment_id=assignment.id,
                student_id=student_id,
                reminder_type="due_soon",
                scheduled_for=scheduled,
                status="pending",
                reason="24-hour deadline reminder",
                dedupe_key=f"due:{assignment.id}:{student_id}:{stamp}",
            )
        )

    def update_deadline(
        self, db: Session, actor: User, assignment_id: int, due_at: datetime
    ) -> Assignment:
        from app.services.authorization import require_teacher_assignment

        assignment = require_teacher_assignment(db, actor, assignment_id)
        if assignment.status == "cancelled":
            raise ValidationError("Cancelled assignments cannot be changed")
        if due_at.tzinfo is None or due_at <= datetime.now(UTC):
            raise ValidationError("New deadline must be a future timezone-aware time")
        old = assignment.due_at
        assignment.due_at = due_at
        assignment.updated_at = datetime.now(UTC)
        pending = db.scalars(
            select(Reminder).where(
                Reminder.assignment_id == assignment.id, Reminder.status == "pending"
            )
        ).all()
        for item in pending:
            item.status = "cancelled"
            item.reason = "Superseded by deadline update"
        student_ids = db.scalars(
            select(AssignmentTarget.student_id).where(
                AssignmentTarget.assignment_id == assignment.id
            )
        ).all()
        for student_id in student_ids:
            self._schedule_due_reminder(db, assignment, student_id)
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=actor.id,
                event_type="deadline_updated",
                entity_type="assignment",
                entity_id=assignment.id,
                metadata_json={"from": old.isoformat(), "to": due_at.isoformat()},
            )
        )
        return assignment

    def clarify(
        self, db: Session, actor: User, assignment_id: int, instructions: str
    ) -> Assignment:
        from app.services.authorization import require_teacher_assignment

        assignment = require_teacher_assignment(db, actor, assignment_id)
        if assignment.status == "cancelled":
            raise ValidationError("Cancelled assignments cannot be changed")
        assignment.instructions = instructions.strip()
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=actor.id,
                event_type="instructions_updated",
                entity_type="assignment",
                entity_id=assignment.id,
                metadata_json={},
            )
        )
        return assignment

    def cancel(self, db: Session, actor: User, assignment_id: int) -> Assignment:
        from app.services.authorization import require_teacher_assignment

        assignment = require_teacher_assignment(db, actor, assignment_id)
        if assignment.status == "cancelled":
            return assignment
        now = datetime.now(UTC)
        assignment.status = "cancelled"
        assignment.cancelled_at = now
        for state in db.scalars(
            select(StudentAssignmentState).where(
                StudentAssignmentState.assignment_id == assignment.id
            )
        ):
            if state.status not in {"completed", "cancelled"}:
                transition(state, "cancelled", now)
        for reminder in db.scalars(
            select(Reminder).where(
                Reminder.assignment_id == assignment.id, Reminder.status == "pending"
            )
        ):
            reminder.status = "cancelled"
            reminder.reason = "Assignment cancelled"
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=actor.id,
                event_type="assignment_cancelled",
                entity_type="assignment",
                entity_id=assignment.id,
                metadata_json={},
            )
        )
        return assignment


def get_student_state(db: Session, assignment_id: int, student_id: int) -> StudentAssignmentState:
    state = db.scalar(
        select(StudentAssignmentState).where(
            StudentAssignmentState.assignment_id == assignment_id,
            StudentAssignmentState.student_id == student_id,
        )
    )
    if not state:
        raise NotFoundError("Assignment state not found")
    return state
