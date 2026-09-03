import pytest
from sqlalchemy import func, select

from app.exceptions import InviteError
from app.models import Assignment
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
