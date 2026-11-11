from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import get_db
from middlewares.auth_middleware import auth_middleware
from model.role_model import Role


def role_name(db: Session, user) -> str | None:
    return db.scalar(select(Role.name).where(Role.id == user.role_id))


def require_roles(*allowed: str):
    """Dependency: the signed-in user must have one of `allowed` roles. Returns the user."""

    def checker(user=Depends(auth_middleware), db: Session = Depends(get_db)):
        if role_name(db, user) not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this action.")
        return user

    return checker


PDMA_STAFF = ("ADMIN", "PDMA_OFFICER")
