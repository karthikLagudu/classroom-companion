from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.exceptions import AuthorizationError, ValidationError
from app.models import (
    ActivityEvent,
    ClassMembership,
    Classroom,
    Invite,
    SchoolMembership,
    User,
)
from app.services.authorization import has_school_role, require_teacher_class


class ClassroomService:
    def add_teacher(
        self,
        db: Session,
        actor: User,
        school_id: int,
        classroom_ids: list[int],
        name: str,
        email: str,
        temporary_password: str,
    ) -> User:
        if not has_school_role(db, actor.id, school_id, {"coordinator"}):
            raise AuthorizationError("Resource is outside your authorized scope")

        clean_name = name.strip()
        clean_email = email.strip().lower()
        selected_ids = sorted(set(classroom_ids))
        if not clean_name or not clean_email or "@" not in clean_email:
            raise ValidationError("A valid teacher name and email are required")
        if len(temporary_password) < 10:
            raise ValidationError("Temporary password must be at least 10 characters")
        if not selected_ids:
            raise ValidationError("Select at least one class for the teacher")
        if db.scalar(select(User).where(User.email == clean_email)):
            raise ValidationError("An account with that email already exists")

        classrooms = list(
            db.scalars(select(Classroom).where(Classroom.id.in_(selected_ids)))
        )
        if len(classrooms) != len(selected_ids) or any(
            classroom.school_id != school_id for classroom in classrooms
        ):
            raise AuthorizationError("Resource is outside your authorized scope")

        teacher = User(
            name=clean_name,
            email=clean_email,
            password_hash=hash_password(temporary_password),
        )
        db.add(teacher)
        db.flush()
        db.add(
            SchoolMembership(school_id=school_id, user_id=teacher.id, role="teacher")
        )
        db.add_all(
            ClassMembership(classroom_id=classroom.id, user_id=teacher.id, role="teacher")
            for classroom in classrooms
        )
        db.add(
            ActivityEvent(
                school_id=school_id,
                classroom_id=None,
                actor_user_id=actor.id,
                event_type="teacher_created",
                entity_type="user",
                entity_id=teacher.id,
                metadata_json={"classroom_ids": selected_ids},
            )
        )
        return teacher

    def create_classroom(
        self, db: Session, actor: User, school_id: int, name: str, grade: str
    ) -> Classroom:
        allowed = has_school_role(db, actor.id, school_id, {"coordinator", "teacher"})
        if not allowed:
            raise AuthorizationError("Resource is outside your authorized scope")
        clean_name, clean_grade = name.strip(), grade.strip()
        if not clean_name or not clean_grade:
            raise ValidationError("Class name and grade are required")
        existing = db.scalar(
            select(Classroom).where(
                Classroom.school_id == school_id, Classroom.name == clean_name
            )
        )
        if existing:
            raise ValidationError("A class with that name already exists")
        classroom = Classroom(school_id=school_id, name=clean_name, grade=clean_grade)
        db.add(classroom)
        db.flush()
        db.add(ClassMembership(classroom_id=classroom.id, user_id=actor.id, role="teacher"))
        return classroom

    def add_student(
        self,
        db: Session,
        actor: User,
        classroom_id: int,
        name: str,
        email: str,
        temporary_password: str,
    ) -> User:
        classroom = require_teacher_class(db, actor, classroom_id)
        clean_email = email.strip().lower()
        if not clean_email or "@" not in clean_email or not name.strip():
            raise ValidationError("A valid name and email are required")
        student = db.scalar(select(User).where(User.email == clean_email))
        if student:
            schools = set(
                db.scalars(
                    select(SchoolMembership.school_id).where(
                        SchoolMembership.user_id == student.id,
                        SchoolMembership.role == "student",
                    )
                )
            )
            if schools and classroom.school_id not in schools:
                raise ValidationError("That student belongs to another school")
        else:
            if len(temporary_password) < 10:
                raise ValidationError("Temporary password must be at least 10 characters")
            student = User(
                name=name.strip(),
                email=clean_email,
                password_hash=hash_password(temporary_password),
            )
            db.add(student)
            db.flush()
        school_membership = db.scalar(
            select(SchoolMembership).where(
                SchoolMembership.school_id == classroom.school_id,
                SchoolMembership.user_id == student.id,
                SchoolMembership.role == "student",
            )
        )
        if not school_membership:
            db.add(
                SchoolMembership(
                    school_id=classroom.school_id, user_id=student.id, role="student"
                )
            )
        membership = db.scalar(
            select(ClassMembership).where(
                ClassMembership.classroom_id == classroom.id,
                ClassMembership.user_id == student.id,
                ClassMembership.role == "student",
            )
        )
        if not membership:
            db.add(ClassMembership(classroom_id=classroom.id, user_id=student.id, role="student"))
        return student

    def create_invite(
        self,
        db: Session,
        actor: User,
        classroom_id: int,
        expires_in_days: int = 7,
        max_uses: int = 20,
    ) -> Invite:
        classroom = require_teacher_class(db, actor, classroom_id)
        if expires_in_days < 1 or max_uses < 1:
            raise ValidationError("Invite expiry and use limit must be positive")
        invite = Invite(
            school_id=classroom.school_id,
            classroom_id=classroom.id,
            code=secrets.token_hex(4).upper(),
            expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
            max_uses=max_uses,
            use_count=0,
            active=True,
        )
        db.add(invite)
        return invite

    def disable_invite(self, db: Session, actor: User, invite_id: int) -> Invite:
        invite = db.get(Invite, invite_id)
        if not invite:
            from app.exceptions import NotFoundError

            raise NotFoundError("Invite not found")
        require_teacher_class(db, actor, invite.classroom_id)
        invite.active = False
        return invite
