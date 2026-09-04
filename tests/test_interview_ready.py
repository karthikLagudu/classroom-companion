from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.auth.security import hash_password
from app.exceptions import AuthorizationError, InviteError
from app.models import (
    ActivityEvent,
    Assignment,
    AssignmentTarget,
    ClassMembership,
    ConversationContext,
    Feedback,
    Invite,
    NotificationDelivery,
    Reminder,
    SchoolMembership,
    StudentAssignmentState,
    Submission,
    User,
)
from app.reminders.policy import next_permitted_time
from app.reminders.scheduler import ReminderScheduler
from app.services.assignment import get_student_state
from app.services.classroom import ClassroomService
from app.services.invite import InviteService
from app.services.submission import FeedbackService, SubmissionService
from app.telegram.conversation import ConversationService
from tests.conftest import authenticate


def local_noon() -> datetime:
    return datetime(2026, 9, 3, 6, 30, tzinfo=UTC)


def add_due_job(db, data, student_key="student1", now=None):
    now = now or local_noon()
    assignment = data["assignment"]
    assignment.due_at = now + timedelta(hours=1)
    row = Reminder(
        assignment_id=assignment.id,
        student_id=data[student_key].id,
        reminder_type="deadline_2h",
        scheduled_for=now,
        status="pending",
        reason="test",
        dedupe_key=f"test:{assignment.id}:{data[student_key].id}:{now.isoformat()}",
        schedule_version=assignment.schedule_version,
    )
    db.add(row)
    db.flush()
    return row


def test_schedule_contains_24h_and_2h_per_student(db, data):
    rows = ReminderScheduler().schedule_assignment(db, data["assignment"], local_noon())
    assert len(rows) == 4
    assert {row.reminder_type for row in rows} == {"deadline_24h", "deadline_2h"}
    assert all(f"version:{data['assignment'].schedule_version}" in row.dedupe_key for row in rows)


def test_quiet_hours_defer_to_school_morning(app, db, data):
    quiet_now = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)  # 23:30 Asia/Kolkata
    data["assignment"].due_at = quiet_now + timedelta(hours=1)
    row = add_due_job(db, data, now=quiet_now)
    result = app.state.reminder_service.run(db, now=quiet_now, classroom_ids={data["class1"].id})
    assert result == [row]
    assert row.status == "deferred"
    assert row.scheduled_for.replace(tzinfo=UTC) == datetime(2026, 9, 4, 1, 30, tzinfo=UTC)
    assert next_permitted_time(quiet_now, "Asia/Kolkata", 22, 7) == row.scheduled_for.replace(tzinfo=UTC)


def test_overdue_is_transitioned_before_policy(app, db, data):
    now = local_noon()
    data["assignment"].due_at = now - timedelta(minutes=1)
    row = add_due_job(db, data, now=now)
    data["assignment"].due_at = now - timedelta(minutes=1)
    app.state.reminder_service.run(db, now=now, classroom_ids={data["class1"].id})
    state = get_student_state(db, data["assignment"].id, data["student1"].id)
    assert state.status == "overdue"
    assert row.reminder_type == "overdue"
    assert db.scalar(
        select(ActivityEvent).where(ActivityEvent.event_type == "student_assignment_overdue")
    )


def test_manual_reminder_scope_excludes_other_school(app, db, data):
    outsider_student = User(
        name="Other Student",
        email="other@student.test",
        password_hash=hash_password("long-test-password"),
        telegram_user_id="303",
        telegram_chat_id="303",
    )
    db.add(outsider_student)
    db.flush()
    db.add_all(
        [
            SchoolMembership(
                school_id=data["school2"].id, user_id=outsider_student.id, role="student"
            ),
            ClassMembership(
                classroom_id=data["class2"].id, user_id=outsider_student.id, role="student"
            ),
        ]
    )
    other = Assignment(
        school_id=data["school2"].id,
        classroom_id=data["class2"].id,
        created_by_user_id=data["teacher2"].id,
        title="Private B",
        instructions="Private",
        status="assigned",
        due_at=local_noon() + timedelta(hours=1),
        timezone="Asia/Kolkata",
    )
    db.add(other)
    db.flush()
    db.add_all(
        [
            AssignmentTarget(assignment_id=other.id, student_id=outsider_student.id),
            StudentAssignmentState(
                assignment_id=other.id, student_id=outsider_student.id, status="assigned"
            ),
        ]
    )
    db.flush()
    ReminderScheduler().schedule_assignment(db, other, local_noon())
    add_due_job(db, data, now=local_noon())
    app.state.reminder_service.run(
        db, now=local_noon(), classroom_ids={data["class1"].id}
    )
    assert not db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.assignment_id == other.id)
    )


def test_dashboard_hides_cross_school_delivery(client, app, db, data):
    secret = "School B private notification body"
    db.add(
        NotificationDelivery(
            user_id=data["teacher2"].id,
            school_id=data["school2"].id,
            classroom_id=data["class2"].id,
            chat_id="102",
            kind="private",
            body=secret,
            status="logged",
        )
    )
    db.commit()
    authenticate(client, app, data["teacher1"].id)
    response = client.get("/teacher")
    assert response.status_code == 200
    assert secret not in response.text


def test_assignment_lifecycle_notifications_and_reschedule(app, db, data):
    service = app.state.assignment_service
    assignment = service.create(
        db,
        data["teacher1"],
        data["class1"].id,
        "Energy",
        "Questions 1-10",
        local_noon() + timedelta(days=3),
        "Asia/Kolkata",
        "create-energy",
    )
    assert len(
        list(
            db.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.assignment_id == assignment.id,
                    NotificationDelivery.kind == "assignment_created",
                )
            )
        )
    ) == 2
    old_version = assignment.schedule_version
    new_due = local_noon() + timedelta(days=4)
    service.update_deadline(db, data["teacher1"], assignment.id, new_due, "move-energy")
    service.update_deadline(db, data["teacher1"], assignment.id, new_due, "move-energy")
    assert assignment.schedule_version == old_version + 1
    assert len(
        list(
            db.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.assignment_id == assignment.id,
                    NotificationDelivery.kind == "deadline_updated",
                )
            )
        )
    ) == 2
    service.clarify(
        db, data["teacher1"], assignment.id, "Include a diagram", "clarify-energy"
    )
    assert db.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.assignment_id == assignment.id,
            NotificationDelivery.kind == "instructions_updated",
        )
    )
    service.cancel(db, data["teacher1"], assignment.id, "cancel-energy")
    service.cancel(db, data["teacher1"], assignment.id, "cancel-energy")
    assert assignment.status == "cancelled"
    assert len(
        list(
            db.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.assignment_id == assignment.id,
                    NotificationDelivery.kind == "assignment_cancelled",
                )
            )
        )
    ) == 2


def test_feedback_double_post_is_idempotent(app, db, data):
    submission = SubmissionService().submit(
        db,
        data["student1"],
        data["assignment"].id,
        "answer-once",
        text_content="answer",
    )
    service = FeedbackService(app.state.notification_service)
    first = service.create(
        db, data["teacher1"], submission.id, "Good work", True, "feedback-once"
    )
    second = service.create(
        db, data["teacher1"], submission.id, "Good work", True, "feedback-once"
    )
    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(Feedback)) == 1


def test_natural_language_teacher_and_student_flows(app, db, data):
    teacher_create = {
        "update_id": 500,
        "message": {
            "from": {"id": 101},
            "chat": {"id": 101},
            "text": "Complete the energy worksheet by tomorrow evening",
        },
    }
    result = app.state.telegram_service.process(db, teacher_create)
    assignment_id = int(str(result["result"]).split(":")[-1])
    db.commit()
    move = {
        "update_id": 501,
        "message": {
            "from": {"id": 101},
            "chat": {"id": 101},
            "text": "Move the energy worksheet to Friday at 6 PM",
        },
    }
    assert "update_assignment_deadline" in str(app.state.telegram_service.process(db, move)["result"])
    for update_id, text, expected in [
        (502, "Got it.", "acknowledged"),
        (503, "I've completed around 60%.", "in_progress"),
        (504, "I'm stuck on question 4.", "blocked"),
    ]:
        update = {
            "update_id": update_id,
            "message": {"from": {"id": 201}, "chat": {"id": 201}, "text": text},
        }
        app.state.telegram_service.process(db, update)
        db.flush()
        assert get_student_state(db, assignment_id, data["student1"].id).status == expected


def test_file_after_message_uses_expiring_context(app, db, data, monkeypatch, tmp_path):
    app.state.telegram_client.settings.upload_dir = tmp_path
    monkeypatch.setattr(
        app.state.telegram_client, "download_file", lambda _file_id, _max_bytes: b"image-bytes"
    )
    first = {
        "update_id": 510,
        "message": {
            "from": {"id": 201},
            "chat": {"id": 201},
            "text": "Here's my homework",
        },
    }
    assert "pending_submission" in str(app.state.telegram_service.process(db, first)["result"])
    second = {
        "update_id": 511,
        "message": {
            "from": {"id": 201},
            "chat": {"id": 201},
            "photo": [{"file_id": "photo-1"}],
        },
    }
    assert "submission" in str(app.state.telegram_service.process(db, second)["result"])
    submission = db.scalar(select(Submission).where(Submission.telegram_file_id == "photo-1"))
    assert submission and submission.stored_file_path


def test_context_expires(db, data):
    service = ConversationService(ttl_minutes=1)
    now = local_noon()
    service.set(
        db,
        data["student1"],
        "201",
        assignment_id=data["assignment"].id,
        now=now,
    )
    assert service.get(db, data["student1"], "201", now + timedelta(minutes=2)) is None
    assert db.scalar(select(func.count()).select_from(ConversationContext)) == 0


@pytest.mark.parametrize("state", ["inactive", "expired", "maxed"])
def test_invalid_invites_are_rejected(db, data, state):
    invite = Invite(
        school_id=data["school1"].id,
        classroom_id=data["class1"].id,
        code=f"BAD{state}",
        active=state != "inactive",
        expires_at=datetime.now(UTC) - timedelta(minutes=1) if state == "expired" else None,
        max_uses=1 if state == "maxed" else 5,
        use_count=1 if state == "maxed" else 0,
    )
    db.add(invite)
    db.flush()
    with pytest.raises(InviteError):
        InviteService().join(db, data["student1"], invite.code, "999", "999")


def test_repeated_join_is_idempotent_but_relink_is_rejected(db, data):
    invite = Invite(
        school_id=data["school1"].id,
        classroom_id=data["class1"].id,
        code="REJOIN",
        active=True,
        max_uses=5,
        use_count=0,
    )
    db.add(invite)
    db.flush()
    InviteService().join(db, data["student1"], invite.code, "201", "201")
    InviteService().join(db, data["student1"], invite.code, "201", "201")
    assert invite.use_count == 0  # membership existed before either join
    with pytest.raises(InviteError, match="already linked"):
        InviteService().join(db, data["student1"], invite.code, "different", "different")


def test_class_creation_requires_school_role(db, data):
    with pytest.raises(AuthorizationError):
        ClassroomService().create_classroom(
            db, data["student1"], data["school2"].id, "Forbidden", "10"
        )


def test_submission_file_is_authorized_for_teacher_and_owner(
    client, app, db, data, tmp_path
):
    app.state.settings.upload_dir = tmp_path
    path = tmp_path / "answer.png"
    path.write_bytes(b"safe-image")
    submission = SubmissionService().submit(
        db,
        data["student1"],
        data["assignment"].id,
        "file-auth",
        stored_file_path=str(path),
        original_filename="answer.png",
        mime_type="image/png",
        content=b"safe-image",
    )
    db.commit()
    authenticate(client, app, data["teacher1"].id)
    assert client.get(f"/submissions/{submission.id}/file").status_code == 200
    authenticate(client, app, data["student1"].id)
    assert client.get(f"/submissions/{submission.id}/file").status_code == 200
    authenticate(client, app, data["student2"].id)
    assert client.get(f"/submissions/{submission.id}/file").status_code == 403
    authenticate(client, app, data["teacher2"].id)
    assert client.get(f"/submissions/{submission.id}/file").status_code == 403


def test_telegram_failure_is_persisted_without_rolling_back(app, db, data):
    client = app.state.telegram_client
    previous_mode = client.settings.telegram_mode
    previous_token = client.settings.telegram_bot_token
    client.settings.telegram_mode = "real"
    client.settings.telegram_bot_token = None
    try:
        delivery = client.send(
            db,
            data["student1"],
            None,
            "Important message",
            school_id=data["school1"].id,
            classroom_id=data["class1"].id,
            assignment_id=data["assignment"].id,
            idempotency_key="failure-evidence",
        )
        assert delivery.status == "failed"
        assert delivery.error == "TELEGRAM_BOT_TOKEN is not configured"
    finally:
        client.settings.telegram_mode = previous_mode
        client.settings.telegram_bot_token = previous_token


def test_malformed_webhook_and_callback_are_safe(client, app, db, data):
    malformed = client.post(
        "/telegram/webhook/test-webhook-secret",
        content="{bad json",
        headers={"content-type": "application/json"},
    )
    assert malformed.status_code == 200
    assert malformed.json()["result"] == "ignored_malformed_json"
    callback = {
        "update_id": 900,
        "callback_query": {
            "from": {"id": 201},
            "data": f"ack:{data['assignment'].id}",
            "message": {"chat": {"id": 201}},
        },
    }
    response = client.post("/telegram/webhook/test-webhook-secret", json=callback)
    assert response.status_code == 200
    assert "acknowledged" in response.json()["result"]
