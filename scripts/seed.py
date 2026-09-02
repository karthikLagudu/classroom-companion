from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.auth.security import hash_password
from app.database import SessionLocal, init_db
from app.models import (
    ClassMembership,
    Classroom,
    Invite,
    School,
    SchoolMembership,
    StudentAssignmentState,
    User,
)
from app.services.assignment import AssignmentService

DEMO_PASSWORD = "DemoPass123!"


def add_user(db, name: str, email: str, telegram_id: str | None = None) -> User:
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(DEMO_PASSWORD),
        telegram_user_id=telegram_id,
        telegram_chat_id=telegram_id,
    )
    db.add(user)
    db.flush()
    return user


def main() -> None:
    init_db()
    with SessionLocal.begin() as db:
        existing = db.scalar(select(School).where(School.name == "SIM Demo School"))
        if existing:
            print("Demo data already exists; nothing changed.")
            return
        school = School(name="SIM Demo School", timezone="Asia/Kolkata")
        other_school = School(name="Northstar Academy", timezone="Asia/Kolkata")
        db.add_all([school, other_school])
        db.flush()
        teacher = add_user(db, "Maya Teacher", "teacher@sim.school", "900001")
        student1 = add_user(db, "Aarav Student", "student1@sim.school", "900101")
        student2 = add_user(db, "Diya Student", "student2@sim.school")
        coordinator = add_user(db, "Ravi Coordinator", "coordinator@sim.school")
        outsider = add_user(db, "Nora Teacher", "teacher@northstar.school")
        class_a = Classroom(school_id=school.id, name="Grade 8 Science", grade="Grade 8")
        class_b = Classroom(school_id=other_school.id, name="Grade 9 Mathematics", grade="Grade 9")
        db.add_all([class_a, class_b])
        db.flush()
        db.add_all(
            [
                SchoolMembership(school_id=school.id, user_id=teacher.id, role="teacher"),
                SchoolMembership(school_id=school.id, user_id=student1.id, role="student"),
                SchoolMembership(school_id=school.id, user_id=student2.id, role="student"),
                SchoolMembership(school_id=school.id, user_id=coordinator.id, role="coordinator"),
                SchoolMembership(school_id=other_school.id, user_id=outsider.id, role="teacher"),
                ClassMembership(classroom_id=class_a.id, user_id=teacher.id, role="teacher"),
                ClassMembership(classroom_id=class_a.id, user_id=student1.id, role="student"),
                ClassMembership(classroom_id=class_a.id, user_id=student2.id, role="student"),
                ClassMembership(classroom_id=class_b.id, user_id=outsider.id, role="teacher"),
                Invite(
                    school_id=school.id,
                    classroom_id=class_a.id,
                    code="SIM8SCI",
                    max_uses=20,
                    use_count=0,
                    active=True,
                ),
            ]
        )
        db.flush()
        assignment = AssignmentService().create(
            db,
            teacher,
            class_a.id,
            "Climate observation journal",
            "Record the weather for two days and upload a photo of your completed journal.",
            datetime.now(UTC) + timedelta(hours=36),
            school.timezone,
            "seed:assignment:1",
        )
        state = db.scalar(
            select(StudentAssignmentState).where(
                StudentAssignmentState.assignment_id == assignment.id,
                StudentAssignmentState.student_id == student1.id,
            )
        )
        state.status = "in_progress"
        state.last_activity_at = datetime.now(UTC)
    print("Seeded Classroom Companion demo data.")
    print("Teacher: teacher@sim.school / DemoPass123!")
    print("Students: student1@sim.school and student2@sim.school / DemoPass123!")
    print("Coordinator: coordinator@sim.school / DemoPass123!")
    print("Invite: SIM8SCI (use /join SIM8SCI student2@sim.school)")


if __name__ == "__main__":
    main()
