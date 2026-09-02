from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import TelegramDeliveryError
from app.models import NotificationDelivery, User

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(
        self,
        db: Session,
        user: User | None,
        chat_id: str | None,
        body: str,
        kind: str = "message",
        reply_markup: dict | None = None,
    ) -> NotificationDelivery:
        target = chat_id or (user.telegram_chat_id if user else None)
        delivery = NotificationDelivery(
            user_id=user.id if user else None,
            chat_id=target,
            kind=kind,
            body=body,
            status="pending",
        )
        db.add(delivery)
        db.flush()
        if not target:
            delivery.status = "skipped"
            delivery.error = "User has not linked Telegram"
            return delivery
        if self.settings.telegram_mode == "log":
            logger.info("telegram_log_delivery chat_id=%s kind=%s body=%s", target, kind, body)
            delivery.status = "logged"
            return delivery
        if not self.settings.telegram_bot_token:
            delivery.status = "failed"
            delivery.error = "TELEGRAM_BOT_TOKEN is not configured"
            raise TelegramDeliveryError(delivery.error)
        try:
            payload = {"chat_id": target, "text": body}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            response = httpx.post(
                f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise TelegramDeliveryError(
                    str(data.get("description", "Telegram rejected message"))
                )
            delivery.status = "sent"
            delivery.external_message_id = str(data["result"]["message_id"])
            return delivery
        except Exception as exc:
            delivery.status = "failed"
            delivery.error = str(exc)[:500]
            logger.exception("telegram_delivery_failed chat_id=%s", target)
            raise TelegramDeliveryError("Telegram delivery failed") from exc
