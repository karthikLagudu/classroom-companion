from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.security import current_session, current_user
from app.database import get_db
from app.models import ClassMembership, Classroom, SchoolMembership, User


def render(request: Request, template: str, **context):
    return request.app.state.templates.TemplateResponse(
        request=request, name=template, context={"request": request, **context}
    )


def web_user(request: Request, db: Session = Depends(get_db)) -> User:
    return current_user(request, db)


def csrf(request: Request) -> str:
    return current_session(request).csrf_token


def operation_key(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"


def teacher_classes(db: Session, user: User) -> list[Classroom]:
    coordinator_school_ids = select(SchoolMembership.school_id).where(
        SchoolMembership.user_id == user.id, SchoolMembership.role == "coordinator"
    )
    teacher_class_ids = select(ClassMembership.classroom_id).where(
        ClassMembership.user_id == user.id, ClassMembership.role == "teacher"
    )
    return list(
        db.scalars(
            select(Classroom)
            .where(
                or_(
                    Classroom.school_id.in_(coordinator_school_ids),
                    Classroom.id.in_(teacher_class_ids),
                )
            )
            .order_by(Classroom.name)
        )
    )
