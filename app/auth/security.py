from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User

PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class SessionData:
    user_id: int
    csrf_token: str


class SessionManager:
    cookie_name = "classroom_session"

    def __init__(self, settings: Settings):
        self.serializer = URLSafeTimedSerializer(
            settings.session_secret, salt="classroom-session-v1"
        )
        self.secure = settings.environment == "production"

    def create(self, user_id: int) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(24)
        return self.serializer.dumps({"uid": user_id, "csrf": csrf}), csrf

    def read(self, value: str | None) -> SessionData | None:
        if not value:
            return None
        try:
            payload = self.serializer.loads(value, max_age=60 * 60 * 24 * 7)
            return SessionData(user_id=int(payload["uid"]), csrf_token=str(payload["csrf"]))
        except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
            return None


def current_session(request: Request) -> SessionData:
    manager: SessionManager = request.app.state.sessions
    data = manager.read(request.cookies.get(manager.cookie_name))
    if not data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return data


def current_user(request: Request, db: Session) -> User:
    data = current_session(request)
    user = db.get(User, data.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return user


def validate_csrf(request: Request, supplied: str) -> None:
    data = current_session(request)
    if not supplied or not secrets.compare_digest(data.csrf_token, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
