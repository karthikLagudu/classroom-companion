from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConversationContext, User


class ConversationService:
    def __init__(self, ttl_minutes: int = 30):
        self.ttl = timedelta(minutes=ttl_minutes)

    def get(
        self, db: Session, user: User, chat_id: str, now: datetime | None = None
    ) -> ConversationContext | None:
        now = now or datetime.now(UTC)
        row = db.scalar(
            select(ConversationContext).where(
                ConversationContext.user_id == user.id,
                ConversationContext.chat_id == chat_id,
            )
        )
        if row and row.expires_at:
            expiry = row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC)
            if expiry <= now:
                db.delete(row)
                db.flush()
                return None
        return row

    def set(
        self,
        db: Session,
        user: User,
        chat_id: str,
        *,
        assignment_id: int | None = None,
        pending_action: str | None = None,
        payload: dict | None = None,
        now: datetime | None = None,
    ) -> ConversationContext:
        now = now or datetime.now(UTC)
        row = self.get(db, user, chat_id, now)
        if not row:
            row = ConversationContext(user_id=user.id, chat_id=chat_id)
            db.add(row)
        row.active_assignment_id = assignment_id
        row.pending_action = pending_action
        row.pending_payload_json = payload
        row.expires_at = now + self.ttl
        row.updated_at = now
        db.flush()
        return row

    def clear(self, db: Session, user: User, chat_id: str) -> None:
        row = self.get(db, user, chat_id)
        if row:
            db.delete(row)
