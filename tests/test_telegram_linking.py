from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func, select

from app.exceptions import AuthorizationError, TelegramLinkError
from app.models import (
    AssignmentTarget,
    NotificationDelivery,
    Reminder,
    SchoolMembership,
    TelegramLinkToken,
)
from app.services.telegram_link import TelegramLinkService
from tests.conftest import authenticate


def raw_token(url: str) -> str:
    return parse_qs(urlparse(url).query)["start"][0]


def unlink(student) -> None:
    student.telegram_user_id = None
    student.telegram_chat_id = None


def link_service(app) -> TelegramLinkService:
    app.state.settings.telegram_bot_username = "classroom_companion_bot"
    return app.state.telegram_link_service


def create_link(app, db, data, student_key="student1", now=None):
    unlink(data[student_key])
    return link_service(app).create_link(
        db,
        data["teacher1"],
        data["class1"].id,
        data[student_key].id,
        now,
    )


def test_tokens_are_unique_hashed_and_new_link_revokes_old(app, db, data):
    service = link_service(app)
    unlink(data["student1"])
    first = service.create_link(
        db, data["teacher1"], data["class1"].id, data["student1"].id
    )
    second = service.create_link(
        db, data["teacher1"], data["class1"].id, data["student1"].id
    )
    rows = list(
        db.scalars(
            select(TelegramLinkToken)
            .where(TelegramLinkToken.user_id == data["student1"].id)
            .order_by(TelegramLinkToken.id)
        )
    )
    assert first.url != second.url
    assert rows[0].revoked_at is not None
    assert rows[1].revoked_at is None
    assert len(rows[1].token_hash) == 64
    assert raw_token(second.url) not in rows[1].token_hash
    with pytest.raises(TelegramLinkError) as revoked_error:
        service.validate_token(db, raw_token(first.url))
    assert revoked_error.value.code == "revoked"


def test_expired_revoked_invalid_and_used_tokens_are_rejected(app, db, data):
    service = link_service(app)
    old = datetime.now(UTC) - timedelta(hours=2)
    expired = create_link(app, db, data, now=old)
    with pytest.raises(TelegramLinkError) as expired_error:
        service.validate_token(db, raw_token(expired.url), datetime.now(UTC))
    assert expired_error.value.code == "expired"

    current = create_link(app, db, data)
    replay_token = raw_token(current.url)
    service.consume_token(db, replay_token, "70002", "70002")
    with pytest.raises(TelegramLinkError) as replay_error:
        service.consume_token(db, replay_token, "70002", "70002")
    assert replay_error.value.code == "used"

    with pytest.raises(TelegramLinkError) as invalid_error:
        service.validate_token(db, "not-a-real-token")
    assert invalid_error.value.code == "invalid"


def test_telegram_and_student_link_conflicts_require_disconnect(app, db, data):
    service = link_service(app)
    link = create_link(app, db, data, "student2")
    with pytest.raises(TelegramLinkError) as telegram_conflict:
        service.consume_token(db, raw_token(link.url), "201", "201")
    assert telegram_conflict.value.code == "telegram_conflict"

    other_link = create_link(app, db, data, "student2")
    data["student2"].telegram_user_id = "already-linked"
    data["student2"].telegram_chat_id = "already-linked"
    with pytest.raises(TelegramLinkError) as student_conflict:
        service.consume_token(db, raw_token(other_link.url), "999", "999")
    assert student_conflict.value.code == "student_conflict"


def test_start_token_links_existing_user_and_cannot_be_replayed(app, db, data):
    link = create_link(app, db, data)
    token = raw_token(link.url)
    update = {
        "update_id": 700,
        "message": {
            "from": {"id": 70001, "username": "not_authoritative"},
            "chat": {"id": 70001, "type": "private"},
            "text": f"/start {token}",
        },
    }
    result = app.state.telegram_service.process(db, update)
    assert result["result"] == f"telegram_link:connected:{data['student1'].id}"
    assert data["student1"].telegram_user_id == "70001"
    assert data["student1"].telegram_chat_id == "70001"
    row = db.get(TelegramLinkToken, link.token_id)
    assert row.used_at is not None

    replay = {**update, "update_id": 701}
    assert app.state.telegram_service.process(db, replay)["result"] == "telegram_link:used"


def test_plain_start_never_guesses_identity(app, db, data):
    update = {
        "update_id": 710,
        "message": {
            "from": {"id": 88888, "username": data["student1"].name},
            "chat": {"id": 88888, "type": "private"},
            "text": "/start",
        },
    }
    result = app.state.telegram_service.process(db, update)
    delivery = db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.chat_id == "88888")
    )
    assert result["result"] == "start_unlinked"
    assert delivery.user_id is None
    assert "secure Telegram connection link" in delivery.body


def test_link_generation_is_teacher_and_coordinator_scoped(app, db, data):
    service = link_service(app)
    unlink(data["student1"])
    assert service.create_link(
        db, data["teacher1"], data["class1"].id, data["student1"].id
    )
    with pytest.raises(AuthorizationError):
        service.create_link(
            db, data["teacher2"], data["class1"].id, data["student1"].id
        )
    db.add(
        SchoolMembership(
            school_id=data["school1"].id,
            user_id=data["teacher2"].id,
            role="coordinator",
        )
    )
    db.flush()
    assert service.create_link(
        db, data["teacher2"], data["class1"].id, data["student1"].id
    )


def test_web_link_and_disconnect_require_csrf_and_hide_bot_token(client, app, db, data):
    link_service(app)
    app.state.settings.telegram_bot_token = "never-show-this-bot-token"
    unlink(data["student1"])
    csrf = authenticate(client, app, data["teacher1"].id)
    path = (
        f"/teacher/classes/{data['class1'].id}/students/"
        f"{data['student1'].id}/telegram-link"
    )
    assert client.post(path, data={"csrf_token": "wrong"}).status_code == 403
    response = client.post(path, data={"csrf_token": csrf})
    assert response.status_code == 200
    assert "https://t.me/classroom_companion_bot?start=" in response.text
    assert "never-show-this-bot-token" not in response.text

    disconnect_path = (
        f"/teacher/classes/{data['class1'].id}/students/"
        f"{data['student1'].id}/telegram-disconnect"
    )
    data["student1"].telegram_user_id = "connected"
    data["student1"].telegram_chat_id = "connected"
    assert client.post(disconnect_path, data={"csrf_token": "wrong"}).status_code == 403
    assert client.post(
        disconnect_path, data={"csrf_token": csrf}, follow_redirects=False
    ).status_code == 303
    assert data["student1"].telegram_user_id is None
    assert data["student1"].telegram_chat_id is None


def test_disconnect_cannot_cross_school_scope(app, db, data):
    with pytest.raises(AuthorizationError):
        link_service(app).disconnect(
            db, data["teacher2"], data["class1"].id, data["student1"].id
        )


def test_assignment_targets_one_student_and_records_exact_delivery(app, db, data):
    assignment = app.state.assignment_service.create(
        db,
        data["teacher1"],
        data["class1"].id,
        "Rahul only",
        "Complete Questions 1-20.",
        datetime.now(UTC) + timedelta(days=2),
        "Asia/Kolkata",
        "one-student-assignment",
        [data["student1"].id],
    )
    targets = list(
        db.scalars(
            select(AssignmentTarget.student_id).where(
                AssignmentTarget.assignment_id == assignment.id
            )
        )
    )
    deliveries = list(
        db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.assignment_id == assignment.id,
                NotificationDelivery.kind == "assignment_created",
            )
        )
    )
    assert targets == [data["student1"].id]
    assert [delivery.user_id for delivery in deliveries] == [data["student1"].id]
    assert "📚 New Homework Assigned" in deliveries[0].body
    assert "Class One" in deliveries[0].body


def test_assignment_targets_two_and_unlinked_student_is_skipped(app, db, data):
    unlink(data["student2"])
    assignment = app.state.assignment_service.create(
        db,
        data["teacher1"],
        data["class1"].id,
        "Both students",
        "Complete the practice set.",
        datetime.now(UTC) + timedelta(days=2),
        "Asia/Kolkata",
        "two-student-assignment",
        [data["student1"].id, data["student2"].id],
    )
    deliveries = list(
        db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.assignment_id == assignment.id,
                NotificationDelivery.kind == "assignment_created",
            )
        )
    )
    assert {delivery.user_id for delivery in deliveries} == {
        data["student1"].id,
        data["student2"].id,
    }
    skipped = next(row for row in deliveries if row.user_id == data["student2"].id)
    assert skipped.status == "skipped"
    assert skipped.error == "User has not linked Telegram"


def test_targeted_reminder_only_uses_assignment_target(app, db, data):
    now = datetime.now(UTC).replace(hour=6, minute=30, second=0, microsecond=0) + timedelta(
        days=1
    )
    assignment = app.state.assignment_service.create(
        db,
        data["teacher1"],
        data["class1"].id,
        "One reminder",
        "Finish it.",
        now + timedelta(hours=1),
        "Asia/Kolkata",
        "one-reminder-assignment",
        [data["student1"].id],
    )
    app.state.reminder_service.run(db, now=now + timedelta(minutes=1))
    reminder_deliveries = list(
        db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.assignment_id == assignment.id,
                NotificationDelivery.kind.in_(
                    ["due_within_2h", "silent_due_soon", "gentle_due_soon"]
                ),
            )
        )
    )
    assert reminder_deliveries
    assert {row.user_id for row in reminder_deliveries} == {data["student1"].id}
    assert db.scalar(
        select(func.count()).select_from(Reminder).where(
            Reminder.assignment_id == assignment.id,
            Reminder.student_id == data["student2"].id,
        )
    ) == 0


@pytest.mark.parametrize("final_state", ["completed", "cancelled"])
def test_final_state_suppresses_targeted_reminder(app, db, data, final_state):
    from app.services.assignment import get_student_state

    now = datetime.now(UTC).replace(hour=6, minute=30, second=0, microsecond=0) + timedelta(
        days=1
    )
    assignment = app.state.assignment_service.create(
        db,
        data["teacher1"],
        data["class1"].id,
        f"Final {final_state}",
        "No reminder should be delivered.",
        now + timedelta(hours=1),
        "Asia/Kolkata",
        f"final-state-{final_state}",
        [data["student1"].id],
    )
    state = get_student_state(db, assignment.id, data["student1"].id)
    state.status = final_state
    if final_state == "cancelled":
        assignment.status = "cancelled"
    app.state.reminder_service.run(db, now=now + timedelta(minutes=1))
    reminder_deliveries = list(
        db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.assignment_id == assignment.id,
                NotificationDelivery.kind.not_in(["assignment_created"]),
            )
        )
    )
    assert reminder_deliveries == []
    assert set(
        db.scalars(
            select(Reminder.status).where(Reminder.assignment_id == assignment.id)
        )
    ) == {"suppressed"}


def test_webhook_rejects_wrong_header_without_processing(client, db):
    response = client.post(
        "/telegram/webhook/test-webhook-secret",
        headers={"x-telegram-bot-api-secret-token": "wrong"},
        json={"update_id": 9999, "message": {"text": "/start"}},
    )
    assert response.status_code == 404
    assert db.scalar(
        select(func.count()).select_from(TelegramLinkToken)
    ) == 0


def test_real_mode_requires_matching_webhook_header(client, app):
    old_mode = app.state.settings.telegram_mode
    app.state.settings.telegram_mode = "real"
    update = {"update_id": 10001}
    try:
        assert client.post(
            "/telegram/webhook/test-webhook-secret", json=update
        ).status_code == 404
        response = client.post(
            "/telegram/webhook/test-webhook-secret",
            headers={"x-telegram-bot-api-secret-token": "test-webhook-secret"},
            json=update,
        )
        assert response.status_code == 200
    finally:
        app.state.settings.telegram_mode = old_mode


def test_invalid_and_expired_start_return_safe_messages(app, db, data):
    invalid = {
        "update_id": 720,
        "message": {
            "from": {"id": 72001},
            "chat": {"id": 72001, "type": "private"},
            "text": "/start invalid-token",
        },
    }
    assert app.state.telegram_service.process(db, invalid)["result"] == "telegram_link:invalid"
    link = create_link(
        app, db, data, now=datetime.now(UTC) - timedelta(hours=2)
    )
    expired = {
        "update_id": 721,
        "message": {
            "from": {"id": 72001},
            "chat": {"id": 72001, "type": "private"},
            "text": f"/start {raw_token(link.url)}",
        },
    }
    assert app.state.telegram_service.process(db, expired)["result"] == "telegram_link:expired"
    bodies = list(
        db.scalars(
            select(NotificationDelivery.body).where(
                NotificationDelivery.chat_id == "72001"
            )
        )
    )
    assert any("invalid" in body for body in bodies)
    assert any("expired" in body for body in bodies)


def test_notification_failure_does_not_rollback_assignment(app, db, data, monkeypatch):
    telegram = app.state.telegram_client
    old_mode = telegram.settings.telegram_mode
    old_token = telegram.settings.telegram_bot_token
    telegram.settings.telegram_mode = "real"
    telegram.settings.telegram_bot_token = "test-token-must-stay-secret"

    def timeout(*_args, **_kwargs):
        raise TimeoutError("network unavailable")

    monkeypatch.setattr(telegram.http, "post", timeout)
    try:
        assignment = app.state.assignment_service.create(
            db,
            data["teacher1"],
            data["class1"].id,
            "Network failure",
            "Assignment remains valid.",
            datetime.now(UTC) + timedelta(days=2),
            "Asia/Kolkata",
            "network-failure-assignment",
            [data["student1"].id],
        )
        db.flush()
        delivery = db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.assignment_id == assignment.id,
                NotificationDelivery.kind == "assignment_created",
            )
        )
        assert assignment.id is not None
        assert delivery.status == "failed"
        assert delivery.next_attempt_at is not None
        assert "test-token-must-stay-secret" not in (delivery.error or "")
    finally:
        telegram.settings.telegram_mode = old_mode
        telegram.settings.telegram_bot_token = old_token


def test_bot_token_never_appears_in_api_response(client, app):
    app.state.settings.telegram_bot_token = "api-secret-bot-token"
    response = client.get("/health")
    assert response.status_code == 200
    assert "api-secret-bot-token" not in response.text


def test_failed_delivery_is_retried_with_mocked_telegram(app, db, data, monkeypatch):
    telegram = app.state.telegram_client
    old_mode = telegram.settings.telegram_mode
    old_token = telegram.settings.telegram_bot_token
    telegram.settings.telegram_mode = "real"
    telegram.settings.telegram_bot_token = "retry-test-token"

    class SuccessfulResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 456}}

    try:
        delivery = NotificationDelivery(
            user_id=data["student1"].id,
            school_id=data["school1"].id,
            classroom_id=data["class1"].id,
            assignment_id=data["assignment"].id,
            chat_id="201",
            kind="retry_test",
            body="Retry safely",
            status="failed",
            attempt_count=1,
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        db.add(delivery)
        db.flush()
        monkeypatch.setattr(
            telegram.http, "post", lambda *_args, **_kwargs: SuccessfulResponse()
        )
        retried = telegram.retry_due(db)
        assert retried == [delivery]
        assert delivery.status == "sent"
        assert delivery.attempt_count == 2
        assert delivery.external_message_id == "456"
        assert delivery.next_attempt_at is None
    finally:
        telegram.settings.telegram_mode = old_mode
        telegram.settings.telegram_bot_token = old_token
