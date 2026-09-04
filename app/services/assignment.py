from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models import (
    ActivityEvent,
    Assignment,
    AssignmentTarget,
    ClassMembership,
    IdempotencyKey,
    StudentAssignmentState,
    User,
)
from app.reminders.scheduler import ReminderScheduler
from app.services.authorization import require_teacher_class
from app.services.notification import NotificationService
from app.services.state_machine import transition


class AssignmentService:
    def __init__(self, notifications: NotificationService | None = None):
        self.notifications = notifications
        self.scheduler = ReminderScheduler()

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
        student_ids: list[int] | None = None,
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
        class_students = list(
            db.scalars(
                select(User)
                .join(ClassMembership, ClassMembership.user_id == User.id)
                .where(
                    ClassMembership.classroom_id == classroom_id, ClassMembership.role == "student"
                )
            )
        )
        if student_ids is None:
            students = class_students
        else:
            requested = set(student_ids)
            students = [student for student in class_students if student.id in requested]
            if requested != {student.id for student in students}:
                raise ValidationError("One or more selected students are outside this class")
        if not students:
            raise ValidationError("Select at least one student")
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
        db.flush()
        self.scheduler.schedule_assignment(db, assignment)
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
        if self.notifications:
            self.notifications.notify_assignment_created(db, assignment)
        return assignment

    def update_deadline(
        self,
        db: Session,
        actor: User,
        assignment_id: int,
        due_at: datetime,
        idempotency_key: str | None = None,
    ) -> Assignment:
        from app.services.authorization import require_teacher_assignment

        assignment = require_teacher_assignment(db, actor, assignment_id)
        if assignment.status == "cancelled":
            raise ValidationError("Cancelled assignments cannot be changed")
        if due_at.tzinfo is None or due_at <= datetime.now(UTC):
            raise ValidationError("New deadline must be a future timezone-aware time")
        if idempotency_key and db.get(IdempotencyKey, idempotency_key):
            return assignment
        old = assignment.due_at
        old_aware = old.replace(tzinfo=old.tzinfo or UTC)
        if old_aware == due_at.astimezone(UTC):
            if idempotency_key:
                db.add(
                    IdempotencyKey(
                        key=idempotency_key,
                        operation="update_deadline",
                        result_reference=str(assignment.id),
                    )
                )
            return assignment
        assignment.due_at = due_at
        assignment.schedule_version += 1
        assignment.updated_at = datetime.now(UTC)
        self.scheduler.reschedule(db, assignment)
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=actor.id,
                event_type="assignment_deadline_updated",
                entity_type="assignment",
                entity_id=assignment.id,
                metadata_json={"from": old.isoformat(), "to": due_at.isoformat()},
            )
        )
        if idempotency_key:
            db.add(
                IdempotencyKey(
                    key=idempotency_key,
                    operation="update_deadline",
                    result_reference=str(assignment.id),
                )
            )
        if self.notifications:
            self.notifications.notify_deadline_changed(db, assignment, old_aware)
        return assignment

    def clarify(
        self,
        db: Session,
        actor: User,
        assignment_id: int,
        instructions: str,
        idempotency_key: str | None = None,
    ) -> Assignment:
        from app.services.authorization import require_teacher_assignment

        assignment = require_teacher_assignment(db, actor, assignment_id)
        if assignment.status == "cancelled":
            raise ValidationError("Cancelled assignments cannot be changed")
        if idempotency_key and db.get(IdempotencyKey, idempotency_key):
            return assignment
        clean = instructions.strip()
        if not clean:
            raise ValidationError("Instructions cannot be empty")
        if assignment.instructions == clean:
            return assignment
        assignment.instructions = clean
        assignment.updated_at = datetime.now(UTC)
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=actor.id,
                event_type="assignment_instructions_updated",
                entity_type="assignment",
                entity_id=assignment.id,
                metadata_json={},
            )
        )
        operation_key = idempotency_key or f"instructions:{uuid.uuid4()}"
        if idempotency_key:
            db.add(
                IdempotencyKey(
                    key=idempotency_key,
                    operation="update_instructions",
                    result_reference=str(assignment.id),
                )
            )
        if self.notifications:
            self.notifications.notify_instructions_changed(db, assignment, operation_key)
        return assignment

    def cancel(
        self,
        db: Session,
        actor: User,
        assignment_id: int,
        idempotency_key: str | None = None,
    ) -> Assignment:
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
        self.scheduler.cancel(db, assignment.id, "Assignment cancelled")
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
        if idempotency_key:
            db.add(
                IdempotencyKey(
                    key=idempotency_key,
                    operation="cancel_assignment",
                    result_reference=str(assignment.id),
                )
            )
        if self.notifications:
            self.notifications.notify_assignment_cancelled(db, assignment)
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
