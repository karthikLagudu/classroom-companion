from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.exceptions import AuthorizationError, NotFoundError
from app.models import (
    Assignment,
    AssignmentTarget,
    ClassMembership,
    Classroom,
    SchoolMembership,
    Submission,
    User,
)


def has_school_role(db: Session, user_id: int, school_id: int, roles: set[str]) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    SchoolMembership.user_id == user_id,
                    SchoolMembership.school_id == school_id,
                    SchoolMembership.role.in_(roles),
                )
            )
        )
    )


def require_teacher_class(db: Session, user: User, classroom_id: int) -> Classroom:
    classroom = db.get(Classroom, classroom_id)
    if not classroom:
        raise NotFoundError("Class not found")
    is_coordinator = has_school_role(db, user.id, classroom.school_id, {"coordinator"})
    is_teacher = bool(
        db.scalar(
            select(
                exists().where(
                    ClassMembership.classroom_id == classroom_id,
                    ClassMembership.user_id == user.id,
                    ClassMembership.role == "teacher",
                )
            )
        )
    )
    if not (is_coordinator or is_teacher):
        raise AuthorizationError("Resource is outside your authorized scope")
    return classroom


def require_student_class(db: Session, user: User, classroom_id: int) -> Classroom:
    classroom = db.get(Classroom, classroom_id)
    if not classroom:
        raise NotFoundError("Class not found")
    allowed = db.scalar(
        select(
            exists().where(
                ClassMembership.classroom_id == classroom_id,
                ClassMembership.user_id == user.id,
                ClassMembership.role == "student",
            )
        )
    )
    if not allowed:
        raise AuthorizationError("Resource is outside your authorized scope")
    return classroom


def require_teacher_assignment(db: Session, user: User, assignment_id: int) -> Assignment:
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise NotFoundError("Assignment not found")
    require_teacher_class(db, user, assignment.classroom_id)
    return assignment


def require_student_assignment(db: Session, user: User, assignment_id: int) -> Assignment:
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise NotFoundError("Assignment not found")
    require_student_class(db, user, assignment.classroom_id)
    targeted = db.scalar(
        select(
            exists().where(
                AssignmentTarget.assignment_id == assignment_id,
                AssignmentTarget.student_id == user.id,
            )
        )
    )
    if not targeted:
        raise AuthorizationError("Resource is outside your authorized scope")
    return assignment


def require_teacher_submission(db: Session, user: User, submission_id: int) -> Submission:
    submission = db.get(Submission, submission_id)
    if not submission:
        raise NotFoundError("Submission not found")
    require_teacher_assignment(db, user, submission.assignment_id)
    return submission


def require_student_submission(db: Session, user: User, submission_id: int) -> Submission:
    submission = db.get(Submission, submission_id)
    if not submission:
        raise NotFoundError("Submission not found")
    if submission.student_id != user.id:
        raise AuthorizationError("Resource is outside your authorized scope")
    require_student_assignment(db, user, submission.assignment_id)
    return submission
