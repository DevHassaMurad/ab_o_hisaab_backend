from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Unauthorized
from app.core.security import create_token, get_current_user, verify_password
from app.models.core import User

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/token")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(func.lower(User.email) == form.username.strip().lower()))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise Unauthorized("BAD_CREDENTIALS", "Email or password is incorrect.")
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": str(user.id), "email": user.email, "full_name": user.full_name, "role": user.role,
            "district_id": str(user.district_id) if user.district_id else None}
