"""Reliable email: write to `email_outbox` in the same transaction as the business change, deliver after.

- `enqueue(db, ...)` only adds a row; the caller's commit saves it with the financial record,
  so a failing mail server never loses or rolls back a payment.
- `deliver_due(bind)` sends due emails; failures are retried after 1, 5, 15, 60, 60 minutes, then marked failed.
- `outbox_worker()` runs `deliver_due` every 30 seconds for the life of the API process.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from config.auth_mail import AuthMailer, MailDeliveryError
from model.fund_model import EmailOutbox

log = logging.getLogger(__name__)
RETRY_MINUTES = (1, 5, 15, 60, 60)
INTERVAL_SECONDS = 30


def now_utc():
    return datetime.utcnow()


def enqueue(db: Session, recipient: str, subject: str, text: str, html: str) -> EmailOutbox:
    row = EmailOutbox(recipient=recipient, subject=subject[:200], text_body=text, html_body=html, status="pending")
    db.add(row)
    return row


def _supports_skip_locked(bind) -> bool:
    """SKIP LOCKED exists in MySQL 8+ and MariaDB 10.6+ (XAMPP ships MariaDB 10.4). Without it the worker still locks
    the rows it sends, it just waits for another sender instead of skipping its rows."""
    d = bind.dialect
    if d.name != "mysql":
        return False  # SQLite (tests) ignores FOR UPDATE anyway
    v = d.server_version_info or ()
    return v >= (10, 6) if getattr(d, "is_mariadb", False) else v >= (8, 0)


def deliver_due(bind, mailer=None, limit=20) -> int:
    """Send every due pending email. Returns how many were sent. Never raises for mail errors."""
    mailer = mailer or AuthMailer()
    sent = 0
    with Session(bind) as db:
        rows = db.scalars(
            select(EmailOutbox)
            .where(EmailOutbox.status == "pending", or_(EmailOutbox.next_attempt_at <= now_utc()))
            .order_by(EmailOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=_supports_skip_locked(db.get_bind()))
        ).all()
        for row in rows:
            row.attempts += 1
            try:
                mailer.deliver(row.recipient, row.subject, row.text_body, row.html_body)
                row.status, row.sent_at, row.last_error = "sent", now_utc(), None
                sent += 1
            except MailDeliveryError as exc:
                row.last_error = str(exc)[:300]
                if row.attempts > len(RETRY_MINUTES):
                    row.status = "failed"
                    log.error("Email %s to %s failed permanently.", row.id, row.recipient)
                else:
                    row.next_attempt_at = now_utc() + timedelta(minutes=RETRY_MINUTES[row.attempts - 1])
            db.commit()
    return sent


async def outbox_worker(bind):
    while True:
        try:
            await asyncio.to_thread(deliver_due, bind)
        except Exception:  # keep the worker alive whatever happens
            log.exception("Email outbox run failed.")
        await asyncio.sleep(INTERVAL_SECONDS)
