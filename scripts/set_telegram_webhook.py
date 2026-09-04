from __future__ import annotations

import httpx

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.telegram_mode != "real":
        raise SystemExit("TELEGRAM_MODE must be set to real before configuring a webhook")
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    if not settings.telegram_bot_username:
        raise SystemExit("TELEGRAM_BOT_USERNAME is required")
    if not settings.telegram_webhook_secret or settings.telegram_webhook_secret == "change-me":
        raise SystemExit("TELEGRAM_WEBHOOK_SECRET must be a strong random value")
    if not settings.base_url.startswith("https://"):
        raise SystemExit("BASE_URL must be a public HTTPS URL")
    url = f"{settings.base_url.rstrip('/')}/telegram/webhook/{settings.telegram_webhook_secret}"
    api_root = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    try:
        response = httpx.post(
            f"{api_root}/setWebhook",
            json={
                "url": url,
                "secret_token": settings.telegram_webhook_secret,
                "drop_pending_updates": False,
                "allowed_updates": ["message", "edited_message", "callback_query"],
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SystemExit(f"Telegram webhook setup failed ({type(exc).__name__})") from None
    if not payload.get("ok"):
        description = str(payload.get("description", "unknown error"))
        description = description.replace(settings.telegram_bot_token, "[redacted]")
        description = description.replace(settings.telegram_webhook_secret, "[redacted]")
        raise SystemExit(f"Telegram rejected webhook setup: {description}")
    try:
        info_response = httpx.get(f"{api_root}/getWebhookInfo", timeout=20)
        info_response.raise_for_status()
        info = info_response.json().get("result", {})
    except (httpx.HTTPError, ValueError):
        info = {}
    if info.get("url") != url:
        raise SystemExit("Webhook was accepted but verification did not return the expected URL")
    print("Telegram webhook configured and verified successfully.")
    print(f"Webhook URL: {settings.base_url.rstrip('/')}/telegram/webhook/***")


if __name__ == "__main__":
    main()
