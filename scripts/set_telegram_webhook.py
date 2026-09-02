from __future__ import annotations

import httpx

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    if not settings.base_url.startswith("https://"):
        raise SystemExit("BASE_URL must be a public HTTPS URL")
    url = f"{settings.base_url.rstrip('/')}/telegram/webhook/{settings.telegram_webhook_secret}"
    response = httpx.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
        json={"url": url, "drop_pending_updates": False},
        timeout=20,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
