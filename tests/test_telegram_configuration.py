from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from scripts import check_telegram_webhook, set_telegram_webhook


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def real_settings() -> Settings:
    return Settings(
        _env_file=None,
        telegram_mode="real",
        telegram_bot_token="unit-test-bot-token",
        telegram_bot_username="@classroom_test_bot",
        telegram_webhook_secret="unit-test-webhook-secret",
        base_url="https://classroom.example",
    )


def test_config_normalizes_bot_username_and_link_ttl():
    settings = real_settings()
    assert settings.telegram_bot_username == "classroom_test_bot"
    assert settings.telegram_link_token_minutes == 30
    assert Settings(
        _env_file=None, telegram_link_token_minutes=5
    ).telegram_link_token_minutes == 5
    assert Settings(
        _env_file=None, telegram_link_token_minutes=1440
    ).telegram_link_token_minutes == 1440


def test_config_rejects_invalid_mode_ttl_and_missing_real_token():
    with pytest.raises(ValidationError, match="TELEGRAM_MODE"):
        Settings(_env_file=None, telegram_mode="unexpected")
    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TOKEN"):
        Settings(_env_file=None, telegram_mode="real", telegram_bot_token=None)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_link_token_minutes=4)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_link_token_minutes=1441)


def test_set_webhook_configures_secret_header_and_redacts_output(monkeypatch, capsys):
    settings = real_settings()
    request = SimpleNamespace(json=None)

    def fake_post(_url, json, timeout):
        request.json = json
        assert timeout == 20
        return FakeResponse({"ok": True, "result": True})

    expected_url = (
        "https://classroom.example/telegram/webhook/unit-test-webhook-secret"
    )
    monkeypatch.setattr(set_telegram_webhook, "get_settings", lambda: settings)
    monkeypatch.setattr(set_telegram_webhook.httpx, "post", fake_post)
    monkeypatch.setattr(
        set_telegram_webhook.httpx,
        "get",
        lambda _url, timeout: FakeResponse(
            {"ok": True, "result": {"url": expected_url}}
        ),
    )
    set_telegram_webhook.main()
    output = capsys.readouterr().out
    assert request.json["url"] == expected_url
    assert request.json["secret_token"] == "unit-test-webhook-secret"
    assert "Webhook URL: https://classroom.example/telegram/webhook/***" in output
    assert "unit-test-bot-token" not in output
    assert "unit-test-webhook-secret" not in output


def test_check_webhook_reports_safe_status(monkeypatch, capsys):
    settings = real_settings()
    payload = {
        "ok": True,
        "result": {
            "url": "https://classroom.example/telegram/webhook/unit-test-webhook-secret",
            "pending_update_count": 2,
            "last_error_date": 1_725_148_800,
            "last_error_message": (
                "request with unit-test-bot-token and unit-test-webhook-secret failed"
            ),
            "allowed_updates": ["message", "callback_query"],
        },
    }
    monkeypatch.setattr(check_telegram_webhook, "get_settings", lambda: settings)
    monkeypatch.setattr(
        check_telegram_webhook.httpx,
        "get",
        lambda _url, timeout: FakeResponse(payload),
    )
    check_telegram_webhook.main()
    output = capsys.readouterr().out
    assert "Webhook URL: https://classroom.example/telegram/webhook/***" in output
    assert "Pending updates: 2" in output
    assert "Allowed updates: message, callback_query" in output
    assert "unit-test-bot-token" not in output
    assert "unit-test-webhook-secret" not in output
