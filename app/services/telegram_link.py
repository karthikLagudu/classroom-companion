from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import AuthorizationError, TelegramLinkError, ValidationError
from app.models import ClassMembership, Classroom, SchoolMembership, TelegramLinkToken, User
from app.services.authorization import require_teacher_class


def aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)


@dataclass(frozen=True)
class TelegramConnectionLink:
    url: str
    expires_at: datetime
    token_id: int


class TelegramLinkService:
    """Owns secure, scoped linking between an internal user and a Telegram account."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _student_for_class(
        db: Session, actor: User, classroom_id: int, student_id: int
    ) -> tuple[Classroom, User]:
        classroom = require_teacher_class(db, actor, classroom_id)
        student = db.scalar(
            select(User)
            .join(ClassMembership, ClassMembership.user_id == User.id)
            .where(
                User.id == student_id,
                ClassMembership.classroom_id == classroom.id,
                ClassMembership.role == "student",
            )
        )
        if not student:
            raise AuthorizationError("Resource is outside your authorized scope")
        return classroom, student

    def revoke_active_tokens(
        self, db: Session, user_id: int, now: datetime | None = None
    ) -> int:
        current = aware_utc(now or datetime.now(UTC))
        rows = list(
            db.scalars(
                select(TelegramLinkToken).where(
                    TelegramLinkToken.user_id == user_id,
                    TelegramLinkToken.used_at.is_(None),
                    TelegramLinkToken.revoked_at.is_(None),
                )
            )
        )
        for row in rows:
            row.revoked_at = current
        return len(rows)

    def create_link(
        self,
        db: Session,
        actor: User,
        classroom_id: int,
        student_id: int,
        now: datetime | None = None,
    ) -> TelegramConnectionLink:
        _, student = self._student_for_class(db, actor, classroom_id, student_id)
        if student.telegram_user_id or student.telegram_chat_id:
            raise ValidationError("Disconnect Telegram before generating a new connection link")
        username = (self.settings.telegram_bot_username or "").strip().lstrip("@")
        if not username:
            raise ValidationError("TELEGRAM_BOT_USERNAME is not configured")
        current = aware_utc(now or datetime.now(UTC))
        self.revoke_active_tokens(db, student.id, current)
        raw_token = secrets.token_urlsafe(24)
        expires_at = current + timedelta(minutes=self.settings.telegram_link_token_minutes)
        row = TelegramLinkToken(
            user_id=student.id,
            token_hash=self._hash(raw_token),
            created_by_user_id=actor.id,
            classroom_id=classroom_id,
            created_at=current,
            expires_at=expires_at,
        )
        db.add(row)
        db.flush()
        return TelegramConnectionLink(
            url=f"https://t.me/{username}?start={raw_token}",
            expires_at=expires_at,
            token_id=row.id,
        )

    def validate_token(
        self, db: Session, raw_token: str, now: datetime | None = None
    ) -> TelegramLinkToken:
        if not raw_token or len(raw_token) > 64:
            raise TelegramLinkError("This Telegram connection link is invalid.", "invalid")
        row = db.scalar(
            select(TelegramLinkToken)
            .where(TelegramLinkToken.token_hash == self._hash(raw_token))
            .with_for_update()
        )
        if not row:
            raise TelegramLinkError("This Telegram connection link is invalid.", "invalid")
        if row.revoked_at is not None:
            raise TelegramLinkError("This Telegram connection link is invalid.", "revoked")
        if row.used_at is not None:
            raise TelegramLinkError("This Telegram connection link has already been used.", "used")
        current = aware_utc(now or datetime.now(UTC))
        if aware_utc(row.expires_at) <= current:
            raise TelegramLinkError("This Telegram connection link has expired.", "expired")
        return row

    def link_user(
        self,
        db: Session,
        token: TelegramLinkToken,
        telegram_user_id: str,
        telegram_chat_id: str,
    ) -> User:
        if (
            not telegram_user_id.isdigit()
            or not telegram_chat_id.lstrip("-").isdigit()
            or telegram_chat_id != telegram_user_id
        ):
            raise TelegramLinkError("Telegram account information is invalid.", "invalid")
        student = db.get(User, token.user_id)
        classroom = db.get(Classroom, token.classroom_id)
        membership = db.scalar(
            select(ClassMembership.id).where(
                ClassMembership.classroom_id == token.classroom_id,
                ClassMembership.user_id == token.user_id,
                ClassMembership.role == "student",
            )
        )
        school_membership = (
            db.scalar(
                select(SchoolMembership.id).where(
                    SchoolMembership.school_id == classroom.school_id,
                    SchoolMembership.user_id == token.user_id,
                    SchoolMembership.role == "student",
                )
            )
            if classroom
            else None
        )
        if not student or not classroom or not membership or not school_membership:
            raise TelegramLinkError("This student can no longer use this connection link.", "invalid")
        other = db.scalar(
            select(User).where(
                User.id != student.id,
                or_(
                    User.telegram_user_id == telegram_user_id,
                    User.telegram_chat_id == telegram_chat_id,
                ),
            )
        )
        if other:
            raise TelegramLinkError(
                "This Telegram account is already connected to another student.",
                "telegram_conflict",
            )
        if (student.telegram_user_id or student.telegram_chat_id) and (
            student.telegram_user_id != telegram_user_id
            or student.telegram_chat_id != telegram_chat_id
        ):
            raise TelegramLinkError(
                "This student is already connected to a different Telegram account. "
                "Ask a teacher to disconnect it first.",
                "student_conflict",
            )
        student.telegram_user_id = telegram_user_id
        student.telegram_chat_id = telegram_chat_id
        return student

    def consume_token(
        self,
        db: Session,
        raw_token: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime | None = None,
    ) -> User:
        token = self.validate_token(db, raw_token, now)
        student = self.link_user(db, token, telegram_user_id, telegram_chat_id)
        token.used_at = aware_utc(now or datetime.now(UTC))
        return student

    def disconnect(
        self,
        db: Session,
        actor: User,
        classroom_id: int,
        student_id: int,
        now: datetime | None = None,
    ) -> User:
        _, student = self._student_for_class(db, actor, classroom_id, student_id)
        student.telegram_user_id = None
        student.telegram_chat_id = None
        self.revoke_active_tokens(db, student.id, now)
        return student
