"""Existing authentication controller: sign-in, registration and account recovery."""

import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash
from sqlalchemy import func, or_, select

from config.auth_mail import AuthMailer, MailDeliveryError
from config.jwt_handler import JwtHandler
from model.auth_challenge_model import AuthChallenge
from model.role_model import Role
from model.user_model import User

GENERIC_MESSAGE = "If an eligible account exists, a verification code has been sent to your email."


class AuthFlowError(Exception):
    def __init__(self, code, detail, status=400):
        self.code, self.detail, self.status = code, detail, status


def now_utc():
    return datetime.now(UTC).replace(tzinfo=None)


class Authentication:
    def __init__(self, db_session, mailer=None):
        self.db_session = db_session
        self.password = PasswordHash.recommended()
        self.jwt_handler = JwtHandler()
        self.mailer = mailer or AuthMailer()

    def register_user(self, data):
        existing_user = (
            self.db_session.execute(
                select(User).where(
                    or_(func.lower(User.email) == data["email"].strip().lower(), User.name == data["user_name"])
                )
            )
            .scalars()
            .first()
        )
        if existing_user:
            return {"error": "User with this email or username already exists."}, 400
        password = self.password.hash(data["password"])

        user = User(name=data["user_name"].strip(), email=data["email"].strip().lower(), password=password)
        self.db_session.add(user)
        self.db_session.commit()
        return {"message": "User registered successfully."}, 201

    def authenticate(self, data):
        user = (
            self.db_session.execute(select(User).where(func.lower(User.email) == data["email"].strip().lower()))
            .scalars()
            .first()
        )

        if user and user.is_active and self.password.verify(data["password"], user.password):
            if not user.email_verified_at:
                return {"error": "Confirm your email address before signing in.", "code": "EMAIL_NOT_VERIFIED"}, 403
            if user.role_id == 0:
                return {
                    "error": "Your account is awaiting role assignment. Contact your administrator.",
                    "code": "ROLE_UNASSIGNED",
                }, 403
            access_token = self.jwt_handler.create_token(user_id=user.id, token_version=user.token_version)

            return {
                "message": "Authentication successful.",
                "access_token": access_token,
                "token_type": "bearer",
                "role": self.db_session.get(Role, user.role_id).name,
            }, 200
        else:
            return {"error": "Invalid email/username or password."}, 401

    def digest(self, value):
        key = os.environ.get("AUTH_CODE_SECRET") or os.environ.get("JWT_SECRET_KEY")
        if not key:
            raise AuthFlowError("AUTH_UNAVAILABLE", "Account verification is not configured.", 503)
        return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()

    def queue_code(self, email, purpose, background_tasks):
        # Respond before SMTP or account lookup so delivery timing cannot reveal accounts.
        try:
            self.mailer.ensure_configured()
        except MailDeliveryError as exc:
            raise AuthFlowError("MAIL_UNAVAILABLE", "Email delivery is temporarily unavailable.", 503) from exc
        background_tasks.add_task(self.deliver_code, self.db_session.get_bind(), email, purpose)
        return {"message": GENERIC_MESSAGE}

    @staticmethod
    def deliver_code(bind, email, purpose):
        from sqlalchemy.orm import Session

        with Session(bind) as db:
            try:
                Authentication(db).request_code(email, purpose)
            except Exception:
                db.rollback()
                logging.getLogger(__name__).error("Verification request could not be processed.")

    def user(self, email):
        return self.db_session.scalar(
            select(User)
            .where(func.lower(User.email) == email.strip().lower())
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def challenge(self, user, purpose):
        return self.db_session.scalar(
            select(AuthChallenge)
            .where(
                AuthChallenge.user_id == user.id,
                AuthChallenge.purpose == purpose,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def request_code(self, email, purpose):
        try:
            self.mailer.ensure_configured()
        except MailDeliveryError as exc:
            raise AuthFlowError(
                "MAIL_UNAVAILABLE", "Email delivery is temporarily unavailable. Please try again later.", 503
            ) from exc
        user = self.user(email)
        if not user or not user.is_active or (purpose == "email_confirmation" and user.email_verified_at):
            self.db_session.rollback()
            return {"message": GENERIC_MESSAGE}
        now = now_utc()
        challenge = self.challenge(user, purpose)
        # Per-account cooldown and rolling window are persisted across restarts.
        # Suppress duplicate sends with the same generic response to avoid enumeration.
        if challenge and (
            (now - challenge.sent_at).total_seconds() < 60
            or (now - challenge.window_start < timedelta(hours=1) and challenge.send_count >= 5)
        ):
            self.db_session.rollback()
            return {"message": GENERIC_MESSAGE}
        code = f"{secrets.randbelow(1_000_000):06d}"
        nonce = secrets.token_urlsafe(32)
        code_hash = self.digest(f"{user.id}:{purpose}:{nonce}:{code}")
        if challenge is None:
            challenge = AuthChallenge(user_id=user.id, purpose=purpose, window_start=now, send_count=0)
            self.db_session.add(challenge)
        if now - challenge.window_start >= timedelta(hours=1):
            challenge.window_start, challenge.send_count = now, 0
        challenge.code_hash, challenge.nonce = code_hash, nonce
        challenge.expires_at, challenge.sent_at = now + timedelta(minutes=10), now
        challenge.send_count += 1
        challenge.attempts = 0
        challenge.verified_at = challenge.consumed_at = None
        challenge.reset_token_hash = challenge.reset_expires_at = None
        try:
            self.mailer.send(user.email, user.name, purpose, code)
            self.db_session.commit()
        except MailDeliveryError:
            self.db_session.rollback()
            # Keep the account-existence response consistent even if a provider fails.
            logging.getLogger(__name__).error("Verification email delivery failed; check SMTP configuration.")
        return {"message": GENERIC_MESSAGE}

    def verify_code(self, email, purpose, code):
        user = self.user(email)
        challenge = self.challenge(user, purpose) if user else None
        now = now_utc()
        if (
            not user
            or not user.is_active
            or not challenge
            or challenge.consumed_at
            or challenge.verified_at
            or (challenge.expires_at <= now or challenge.attempts >= 5)
        ):
            self.db_session.rollback()
            raise AuthFlowError("INVALID_CODE", "This code is invalid or expired. Request a new code.")
        expected = self.digest(f"{user.id}:{purpose}:{challenge.nonce}:{code}")
        if not hmac.compare_digest(expected, challenge.code_hash):
            challenge.attempts += 1
            self.db_session.commit()
            raise AuthFlowError("INVALID_CODE", "This code is invalid or expired. Request a new code.")
        challenge.verified_at = now
        if purpose == "email_confirmation":
            user.email_verified_at = now
            challenge.consumed_at = now
            self.db_session.commit()
            return {"message": "Email confirmed. You can now sign in."}
        token = secrets.token_urlsafe(32)
        challenge.reset_token_hash = self.digest(token)
        challenge.reset_expires_at = now + timedelta(minutes=10)
        self.db_session.commit()
        return {"reset_token": token, "expires_in": 600}

    @staticmethod
    def validate_password(password):
        if len(password) < 12 or len(password) > 128:
            raise AuthFlowError("PASSWORD_TOO_WEAK", "Use a password between 12 and 128 characters.", 422)

    def reset_password(self, email, token, password):
        self.validate_password(password)
        user = self.user(email)
        challenge = self.challenge(user, "password_reset") if user else None
        now = now_utc()
        if (
            not user
            or not user.is_active
            or not challenge
            or not challenge.verified_at
            or challenge.consumed_at
            or (not challenge.reset_expires_at or challenge.reset_expires_at <= now or not challenge.reset_token_hash)
            or not hmac.compare_digest(self.digest(token), challenge.reset_token_hash)
        ):
            self.db_session.rollback()
            raise AuthFlowError("INVALID_RESET_TOKEN", "Your reset session is invalid or expired. Request a new code.")
        self._save_password(user, password)
        return {"message": "Password reset successfully. Sign in with your new password."}

    def change_password(self, user, current_password, new_password):
        self.validate_password(new_password)
        locked = self.db_session.scalar(
            select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
        )
        if not self.password.verify(current_password, locked.password):
            self.db_session.rollback()
            raise AuthFlowError("WRONG_CURRENT_PASSWORD", "Your current password is incorrect.", 400)
        self._save_password(locked, new_password)
        return {"message": "Password changed. Please sign in again."}

    def _save_password(self, user, password):
        if self.password.verify(password, user.password):
            self.db_session.rollback()
            raise AuthFlowError("PASSWORD_REUSED", "Choose a password different from your current password.", 422)
        user.password = self.password.hash(password)
        user.token_version += 1
        # A password reset proves control of the same email address.
        user.email_verified_at = user.email_verified_at or now_utc()
        for challenge in self.db_session.scalars(select(AuthChallenge).where(AuthChallenge.user_id == user.id)):
            challenge.consumed_at = now_utc()
            challenge.reset_token_hash = None
        recipient, name = user.email, user.name
        self.db_session.commit()
        try:
            self.mailer.send(recipient, name, "password_changed")
        except MailDeliveryError:
            logging.getLogger(__name__).error("Password-change notification delivery failed.")
