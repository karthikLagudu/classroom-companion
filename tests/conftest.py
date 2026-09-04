from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.security import hash_password
from app.database import Base, get_db
from app.main import create_app
from app.models import (
    Assignment,
    AssignmentTarget,
    ClassMembership,
    Classroom,
    Invite,
    School,
    SchoolMembership,
    StudentAssignmentState,
    User,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
        session.rollback()
    Base.metadata.drop_all(engine)


@pytest.fixture
def data(db: Session) -> dict[str, object]:
    password = hash_password("test-password")
    school1 = School(name="School One", timezone="Asia/Kolkata")
    school2 = School(name="School Two", timezone="Asia/Kolkata")
    db.add_all([school1, school2])
    db.flush()
    teacher1 = User(
        name="Teacher One",
        email="t1@example.test",
        password_hash=password,
        telegram_user_id="101",
        telegram_chat_id="101",
    )
    teacher2 = User(
        name="Teacher Two",
        email="t2@example.test",
        password_hash=password,
        telegram_user_id="102",
        telegram_chat_id="102",
    )
    student1 = User(
        name="Student One",
        email="s1@example.test",
        password_hash=password,
        telegram_user_id="201",
        telegram_chat_id="201",
    )
    student2 = User(
        name="Student Two",
        email="s2@example.test",
        password_hash=password,
        telegram_user_id="202",
        telegram_chat_id="202",
    )
    db.add_all([teacher1, teacher2, student1, student2])
    db.flush()
    class1 = Classroom(school_id=school1.id, name="Class One", grade="8")
    class2 = Classroom(school_id=school2.id, name="Class Two", grade="9")
    db.add_all([class1, class2])
    db.flush()
    db.add_all(
        [
            SchoolMembership(school_id=school1.id, user_id=teacher1.id, role="teacher"),
            SchoolMembership(school_id=school2.id, user_id=teacher2.id, role="teacher"),
            SchoolMembership(school_id=school1.id, user_id=student1.id, role="student"),
            SchoolMembership(school_id=school1.id, user_id=student2.id, role="student"),
            ClassMembership(classroom_id=class1.id, user_id=teacher1.id, role="teacher"),
            ClassMembership(classroom_id=class2.id, user_id=teacher2.id, role="teacher"),
            ClassMembership(classroom_id=class1.id, user_id=student1.id, role="student"),
            ClassMembership(classroom_id=class1.id, user_id=student2.id, role="student"),
        ]
    )
    db.flush()
    assignment = Assignment(
        school_id=school1.id,
        classroom_id=class1.id,
        created_by_user_id=teacher1.id,
        title="Essay",
        instructions="Write an essay",
        status="assigned",
        due_at=datetime.now(UTC) + timedelta(days=2),
        timezone="Asia/Kolkata",
    )
    db.add(assignment)
    db.flush()
    db.add_all(
        [
            AssignmentTarget(assignment_id=assignment.id, student_id=student1.id),
            AssignmentTarget(assignment_id=assignment.id, student_id=student2.id),
            StudentAssignmentState(
                assignment_id=assignment.id, student_id=student1.id, status="assigned"
            ),
            StudentAssignmentState(
                assignment_id=assignment.id, student_id=student2.id, status="assigned"
            ),
            Invite(
                school_id=school2.id, classroom_id=class2.id, code="OTHER", active=True, use_count=0
            ),
        ]
    )
    db.commit()
    return locals()


@pytest.fixture
def app(db: Session):
    application = create_app()
    application.state.settings.telegram_mode = "log"
    application.state.settings.telegram_bot_token = None
    application.state.settings.telegram_bot_username = "classroom_test_bot"
    application.state.settings.telegram_webhook_secret = "test-webhook-secret"
    application.state.settings.base_url = "http://127.0.0.1:8000"
    application.state.session_factory = sessionmaker(
        bind=db.get_bind(), expire_on_commit=False, autoflush=False
    )

    def override_db():
        yield db

    application.dependency_overrides[get_db] = override_db
    return application


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def authenticate(client: TestClient, app, user_id: int) -> str:
    token, csrf = app.state.sessions.create(user_id)
    client.cookies.set(app.state.sessions.cookie_name, token)
    return csrf
