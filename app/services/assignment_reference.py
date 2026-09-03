from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.exceptions import AmbiguousReferenceError, NotFoundError
from app.models import Assignment, AssignmentTarget, ClassMembership, SchoolMembership, User


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


class AssignmentReferenceService:
    def _choose(self, assignments: list[Assignment], reference: str) -> Assignment:
        clean = normalize(reference)
        explicit = re.fullmatch(r"#?(\d+)", reference.strip())
        if explicit:
            matches = [item for item in assignments if item.id == int(explicit.group(1))]
        else:
            exact = [item for item in assignments if normalize(item.title) == clean]
            if exact:
                matches = exact
            else:
                prefix = [item for item in assignments if normalize(item.title).startswith(clean)]
                if prefix:
                    matches = prefix
                else:
                    tokens = set(clean.split())
                    scored = [
                        (len(tokens & set(normalize(item.title).split())), item)
                        for item in assignments
                    ]
                    best = max((score for score, _ in scored), default=0)
                    matches = [item for score, item in scored if score == best and score > 0]
        if not matches:
            raise NotFoundError("No authorized assignment matches that reference")
        if len(matches) > 1:
            choices = ", ".join(f"#{item.id} {item.title}" for item in matches[:5])
            raise AmbiguousReferenceError(f"Which assignment do you mean? {choices}")
        return matches[0]

    def resolve_for_teacher(
        self,
        db: Session,
        actor: User,
        reference: str,
        classroom_id: int | None = None,
    ) -> Assignment:
        coordinator_schools = select(SchoolMembership.school_id).where(
            SchoolMembership.user_id == actor.id,
            SchoolMembership.role == "coordinator",
        )
        teacher_classes = select(ClassMembership.classroom_id).where(
            ClassMembership.user_id == actor.id,
            ClassMembership.role == "teacher",
        )
        query = select(Assignment).where(
            or_(
                Assignment.school_id.in_(coordinator_schools),
                Assignment.classroom_id.in_(teacher_classes),
            ),
            Assignment.status == "assigned",
        )
        if classroom_id is not None:
            query = query.where(Assignment.classroom_id == classroom_id)
        return self._choose(list(db.scalars(query)), reference)

    def resolve_for_student(self, db: Session, actor: User, reference: str) -> Assignment:
        query = (
            select(Assignment)
            .join(AssignmentTarget, AssignmentTarget.assignment_id == Assignment.id)
            .where(
                AssignmentTarget.student_id == actor.id,
                Assignment.status == "assigned",
            )
        )
        return self._choose(list(db.scalars(query)), reference)
