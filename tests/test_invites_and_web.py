import pytest
from sqlalchemy import func, select

from app.auth.security import verify_password
from app.exceptions import InviteError
from app.models import Assignment, ClassMembership, SchoolMembership, User
from app.services.invite import InviteService
from tests.conftest import authenticate


def test_invite_cannot_cross_school_boundary(db, data):
    with pytest.raises(InviteError, match="different school"):
        InviteService().join(db, data["student1"], "OTHER", "9999", "9999")


def test_web_double_submit_creates_one_assignment(client, app, db, data):
    csrf = authenticate(client, app, data["teacher1"].id)
    payload = {
        "classroom_id": data["class1"].id,
        "natural_text": "Complete the worksheet by tomorrow evening",
        "idempotency_key": "web-double-click",
        "csrf_token": csrf,
    }
    before = db.scalar(select(func.count()).select_from(Assignment))
    first = client.post("/teacher/assignments", data=payload, follow_redirects=False)
    second = client.post("/teacher/assignments", data=payload, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert db.scalar(select(func.count()).select_from(Assignment)) == before + 1


def test_state_changing_web_request_requires_csrf(client, app, data):
    authenticate(client, app, data["teacher1"].id)
    response = client.post("/teacher/reminders/run", data={"csrf_token": "wrong"})
    assert response.status_code == 403


def test_coordinator_has_school_dashboard(client, app, db, data):
    from app.models import SchoolMembership

    db.add(
        SchoolMembership(
            school_id=data["school1"].id,
            user_id=data["teacher1"].id,
            role="coordinator",
        )
    )
    db.commit()
    authenticate(client, app, data["teacher1"].id)
    response = client.get("/coordinator")
    assert response.status_code == 200
    assert "Create a class" in response.text


def test_teacher_class_metric_opens_authorized_class_menu(client, app, data):
    authenticate(client, app, data["teacher1"].id)

    response = client.get("/teacher")

    assert response.status_code == 200
    assert 'aria-controls="class-switcher-menu"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert f'href="/teacher/classes/{data["class1"].id}"' in response.text
    assert data["class1"].name in response.text
    assert f'href="/teacher/classes/{data["class2"].id}"' not in response.text


def test_coordinator_creates_scoped_teacher_profile(client, app, db, data):
    db.add(
        SchoolMembership(
            school_id=data["school1"].id,
            user_id=data["student1"].id,
            role="coordinator",
        )
    )
    db.commit()
    csrf = authenticate(client, app, data["student1"].id)

    response = client.post(
        "/coordinator/teachers",
        data={
            "school_id": data["school1"].id,
            "classroom_ids": data["class1"].id,
            "name": "New Teacher",
            "email": "new.teacher@example.test",
            "temporary_password": "temporary-pass-123",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    teacher = db.scalar(select(User).where(User.email == "new.teacher@example.test"))
    assert teacher is not None
    assert verify_password("temporary-pass-123", teacher.password_hash)
    assert db.scalar(
        select(SchoolMembership).where(
            SchoolMembership.school_id == data["school1"].id,
            SchoolMembership.user_id == teacher.id,
            SchoolMembership.role == "teacher",
        )
    )
    assert db.scalar(
        select(ClassMembership).where(
            ClassMembership.classroom_id == data["class1"].id,
            ClassMembership.user_id == teacher.id,
            ClassMembership.role == "teacher",
        )
    )


def test_regular_teacher_cannot_create_teacher_profile(client, app, db, data):
    csrf = authenticate(client, app, data["teacher1"].id)

    response = client.post(
        "/coordinator/teachers",
        data={
            "school_id": data["school1"].id,
            "classroom_ids": data["class1"].id,
            "name": "Forbidden Teacher",
            "email": "forbidden.teacher@example.test",
            "temporary_password": "temporary-pass-123",
            "csrf_token": csrf,
        },
    )

    assert response.status_code == 403
    assert db.scalar(select(User).where(User.email == "forbidden.teacher@example.test")) is None


def test_coordinator_cannot_assign_teacher_to_another_school(client, app, db, data):
    db.add(
        SchoolMembership(
            school_id=data["school1"].id,
            user_id=data["student1"].id,
            role="coordinator",
        )
    )
    db.commit()
    csrf = authenticate(client, app, data["student1"].id)

    response = client.post(
        "/coordinator/teachers",
        data={
            "school_id": data["school1"].id,
            "classroom_ids": data["class2"].id,
            "name": "Cross School Teacher",
            "email": "cross-school@example.test",
            "temporary_password": "temporary-pass-123",
            "csrf_token": csrf,
        },
    )

    assert response.status_code == 403
    assert db.scalar(select(User).where(User.email == "cross-school@example.test")) is None


def test_teacher_profile_creation_requires_csrf(client, app, db, data):
    db.add(
        SchoolMembership(
            school_id=data["school1"].id,
            user_id=data["student1"].id,
            role="coordinator",
        )
    )
    db.commit()
    authenticate(client, app, data["student1"].id)

    response = client.post(
        "/coordinator/teachers",
        data={
            "school_id": data["school1"].id,
            "classroom_ids": data["class1"].id,
            "name": "No CSRF Teacher",
            "email": "no-csrf@example.test",
            "temporary_password": "temporary-pass-123",
            "csrf_token": "invalid",
        },
    )

    assert response.status_code == 403
    assert db.scalar(select(User).where(User.email == "no-csrf@example.test")) is None
