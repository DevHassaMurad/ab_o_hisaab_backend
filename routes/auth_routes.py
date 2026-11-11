from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, ValidationError, model_validator
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from config.database import get_db
from controller.authentication import GENERIC_MESSAGE, Authentication, AuthFlowError
from middlewares.auth_middleware import auth_middleware
from model.role_model import Role
from model.user_model import User

# Per-process IP burst protection complements persisted per-account code limits.
_buckets = defaultdict(deque)
_bucket_lock = Lock()


def auth_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = monotonic()
    with _bucket_lock:
        if ip not in _buckets and len(_buckets) >= 4096:
            expired = [key for key, value in _buckets.items() if not value or value[-1] <= now - 60]
            for key in expired:
                del _buckets[key]
            if len(_buckets) >= 4096:
                raise HTTPException(429, "Too many requests. Please try again shortly.", headers={"Retry-After": "60"})
        bucket = _buckets[ip]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= 30:
            raise HTTPException(429, "Too many requests. Please try again shortly.", headers={"Retry-After": "60"})
        bucket.append(now)


router = APIRouter(prefix="/auth", tags=["Authentication"], dependencies=[Depends(auth_rate_limit)])


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=100)
    user_name: str = ""
    password: str = Field(min_length=1, max_length=128)


class EmailRequest(BaseModel):
    email: EmailStr = Field(max_length=100)


class VerifyRequest(EmailRequest):
    code: str = Field(pattern=r"^\d{6}$", min_length=6, max_length=6)


class ResetRequest(EmailRequest):
    reset_token: str = Field(min_length=40, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("The passwords do not match.")
        return self


class RegisterRequest(EmailRequest):
    user_name: str = Field(min_length=1, max_length=50, pattern=r".*\S.*")
    password: str = Field(min_length=12, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


def login_response(data, db):
    try:
        data = LoginRequest.model_validate(data).model_dump()
    except ValidationError:
        return JSONResponse({"detail": "Email or password is incorrect.", "code": "BAD_CREDENTIALS"}, status_code=401)
    response, status = Authentication(db).authenticate(data)
    if status != 200:
        return JSONResponse(
            {"detail": response["error"], "code": response.get("code", "BAD_CREDENTIALS")}, status_code=status
        )
    return response


@router.post("/authenticate")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    return login_response(request.model_dump(), db)


@router.post("/token")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return login_response({"email": form.username, "password": form.password}, db)


@router.post("/password-reset/request", status_code=202)
def request_password_reset(request: EmailRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    return Authentication(db).queue_code(str(request.email), "password_reset", background_tasks)


@router.post("/password-reset/verify")
def verify_password_reset(request: VerifyRequest, db: Session = Depends(get_db)):
    return Authentication(db).verify_code(str(request.email), "password_reset", request.code)


@router.post("/password-reset/confirm")
def confirm_password_reset(request: ResetRequest, db: Session = Depends(get_db)):
    return Authentication(db).reset_password(str(request.email), request.reset_token, request.new_password)


@router.post("/email-verification/request", status_code=202)
def request_email_confirmation(request: EmailRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    return Authentication(db).queue_code(str(request.email), "email_confirmation", background_tasks)


@router.post("/email-verification/verify")
def verify_email_confirmation(request: VerifyRequest, db: Session = Depends(get_db)):
    return Authentication(db).verify_code(str(request.email), "email_confirmation", request.code)


@router.post("/password")
def change_password(request: ChangePasswordRequest, user=Depends(auth_middleware), db: Session = Depends(get_db)):
    return Authentication(db).change_password(user, request.current_password, request.new_password)


@router.post("/logout")
def logout(user=Depends(auth_middleware), db: Session = Depends(get_db)):
    db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    db.commit()
    return {"message": "Signed out of all sessions."}


@router.get("/me")
def me(user=Depends(auth_middleware), db: Session = Depends(get_db)):
    role = db.scalar(select(Role.name).where(Role.id == user.role_id))
    if role not in {"ADMIN", "DC", "DONOR", "NGO", "PDMA_OFFICER"}:
        raise AuthFlowError(
            "ROLE_UNASSIGNED", "Your account is awaiting role assignment. Contact your administrator.", 403
        )
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.name,
        "role": role,
        "district_id": user.district_id,
        "image": user.image,
        "email_verified": bool(user.email_verified_at),
        "registration_status": user.registration_status,
    }


@router.get("/protected")
def protected_route(user=Depends(auth_middleware)):
    return {"message": f"Hello, {user.name}! You have accessed a protected route."}


@router.get("/health")
def auth_health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
