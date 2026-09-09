import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import DBSession
from app.core.config import Settings
from app.core.errors import DomainError
from app.models import Employee, User
from app.schemas.auth import SessionRead, UserRead

bearer = HTTPBearer(auto_error=False, scheme_name="DemoBearerSession")


def active_user(session: Session, user_id: UUID) -> User:
    user = session.scalar(
        select(User)
        .join(Employee, Employee.id == User.employee_id)
        .where(User.id == user_id, Employee.is_active.is_(True))
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise DomainError(401, "invalid_session", "An active user session is required.")
    return user


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _signature(payload: str, settings: Settings) -> str:
    return _encode(
        hmac.new(
            settings.session_signing_key.get_secret_value().encode(),
            payload.encode(),
            hashlib.sha256,
        ).digest()
    )


def issue_session(user: User, settings: Settings) -> SessionRead:
    now = int(time.time())
    expires = now + settings.session_ttl_seconds
    claims = {
        "v": 1,
        "sub": str(user.id),
        "iat": now,
        "exp": expires,
        "nonce": secrets.token_hex(16),
    }
    payload = _encode(json.dumps(claims, separators=(",", ":")).encode())
    return SessionRead(
        access_token=f"{payload}.{_signature(payload, settings)}",
        expires_at=datetime.fromtimestamp(expires, UTC),
        user=UserRead.model_validate(user),
    )


def current_user(
    request: Request,
    session: DBSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    try:
        if credentials is None:
            raise ValueError("Missing bearer session")
        token = credentials.credentials
        if len(token) > 2048:
            raise ValueError("Invalid bearer token")
        payload, signature = token.split(".")
        settings = request.app.state.settings
        if not hmac.compare_digest(signature, _signature(payload, settings)):
            raise ValueError("Invalid signature")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(claims, dict) or set(claims) != {"v", "sub", "iat", "exp", "nonce"}:
            raise ValueError("Invalid claims")
        if (
            claims["v"] != 1
            or type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or not 0 <= claims["iat"] <= time.time() < claims["exp"]
            or claims["exp"] - claims["iat"] > 86400
        ):
            raise ValueError("Expired token")
        user_id = UUID(claims["sub"])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise DomainError(
            401, "invalid_session", "A valid unexpired bearer session is required."
        ) from None
    return active_user(session, user_id)


def require_demo_mode(settings: Settings) -> None:
    if not settings.demo_auth_enabled or settings.app_env == "production":
        raise DomainError(404, "demo_disabled", "Demo identity selection is disabled.")
