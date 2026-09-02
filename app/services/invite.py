from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import InviteError
from app.models import ClassMembership, Classroom, Invite, SchoolMembership, User


class InviteService:
    def join(
        self, db: Session, user: User, code: str, telegram_user_id: str, chat_id: str
    ) -> Classroom:
        invite = db.scalar(select(Invite).where(Invite.code == code.strip().upper()))
        now = datetime.now(UTC)
        if not invite or not invite.active:
            raise InviteError("Invite code is invalid or inactive")
        expires = invite.expires_at
        if expires and expires.replace(tzinfo=expires.tzinfo or UTC) < now:
            raise InviteError("Invite code has expired")
        if invite.max_uses is not None and invite.use_count >= invite.max_uses:
            raise InviteError("Invite code has reached its use limit")
        classroom = db.get(Classroom, invite.classroom_id)
        if not classroom or classroom.school_id != invite.school_id:
            raise InviteError("Invite context is invalid")
        school_ids = set(
            db.scalars(
                select(SchoolMembership.school_id).where(
                    SchoolMembership.user_id == user.id, SchoolMembership.role == "student"
                )
            )
        )
        if school_ids and invite.school_id not in school_ids:
            raise InviteError("Invite is for a different school")
        existing_tg = db.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id, User.id != user.id)
        )
        if existing_tg:
            raise InviteError("This Telegram account is already linked")
        existing = db.scalar(
            select(ClassMembership).where(
                ClassMembership.classroom_id == classroom.id,
                ClassMembership.user_id == user.id,
                ClassMembership.role == "student",
            )
        )
        if not existing:
            db.add(ClassMembership(classroom_id=classroom.id, user_id=user.id, role="student"))
            invite.use_count += 1
        if not school_ids:
            db.add(SchoolMembership(school_id=invite.school_id, user_id=user.id, role="student"))
        user.telegram_user_id = telegram_user_id
        user.telegram_chat_id = chat_id
        return classroom
