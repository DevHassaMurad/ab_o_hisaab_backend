"""Password hashing, JWT tokens and role guards (NFR6)."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import Forbidden, Unauthorized
from app.models.core import User
from app.models.enums import Role

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    now = datetime.now(UTC)
    district = str(user.district_id) if user.district_id else None
    claims = {"sub": str(user.id), "role": user.role, "district_id": district,
              "iat": now, "exp": now + timedelta(minutes=settings.JWT_TTL_MIN)}
    return jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256")


def get_current_user(token: str | None = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    if not token:
        raise Unauthorized("NOT_AUTHENTICATED", "Sign in first.")
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise Unauthorized("INVALID_TOKEN", "Your session is invalid or has expired.") from None
    user = db.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active or user.role != claims.get("role"):
        raise Unauthorized("INVALID_TOKEN", "Your session is invalid or has expired.")
    return user


def require_role(*roles: Role) -> Callable[..., User]:
    allowed = {str(r) for r in roles}

    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise Forbidden("ROLE_FORBIDDEN", "Your role cannot use this endpoint.")
        return user

    dep.__roles__ = tuple(allowed)  # type: ignore[attr-defined]
    return dep


def assert_district_access(user: User, district_id: uuid.UUID) -> None:
    if user.role == Role.DC and user.district_id != district_id:
        raise Forbidden("FORBIDDEN_DISTRICT", "Deputy Commissioners can only see their own district.")
