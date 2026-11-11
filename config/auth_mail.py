"""SMTP transactional mail with accessible HTML and a plain-text alternative."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from html import escape


class MailDeliveryError(Exception):
    pass


def render_email(name, purpose, code=None):
    confirmation = purpose == "email_confirmation"
    changed = purpose == "password_changed"
    title = (
        "Your password has been changed"
        if changed
        else ("Confirm your email address" if confirmation else "Reset your password")
    )
    intro = (
        "The password for your Aab o Hisaab account was changed. All previous sessions have been signed out."
        if changed
        else (
            "Confirm this email address to finish setting up your Aab o Hisaab account."
            if confirmation
            else "We received a request to reset your Aab o Hisaab password."
        )
    )
    instruction = (
        "If you did not make this change, contact your administrator immediately."
        if changed
        else "Enter this six-digit code in the portal. It expires in 10 minutes and can only be used once."
    )
    text = f"Aab o Hisaab\n\n{title}\n\nHello {name},\n\n{intro}\n\n{instruction}\n"
    if code:
        text += f"\nYour verification code: {code}\n"
    text += "\nNever share your password or verification code.\n"
    if not changed:
        text += "If you did not request this email, you can safely ignore it.\n"
    code_html = (
        f'<div style="margin:28px 0;padding:24px;background:#f1f6f4;border:1px solid #dce7e2;'
        f"border-radius:8px;text-align:center;font-size:32px;font-weight:700;letter-spacing:8px;"
        f'color:#0F4C5C">{escape(code)}</div>'
        if code
        else ""
    )
    ignored_notice = (
        "<p style='font-size:13px;line-height:1.7;color:#4A5664'>"
        "If you did not request this email, you can safely ignore it.</p>"
        if not changed
        else ""
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F5F7;font-family:Arial,Helvetica,sans-serif;color:#121A24">
<div style="display:none;max-height:0;overflow:hidden">{escape(title)} — Aab o Hisaab account security</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:40px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="max-width:560px;margin:auto;background:#fff;
border:1px solid #dfe6e2;border-radius:12px">
<tr><td style="padding:28px 36px;border-bottom:1px solid #e6ece8;font-size:20px;font-weight:700;
color:#0F4C5C">Aab o Hisaab</td></tr>
<tr><td style="padding:32px 36px">
<p style="margin:0 0 12px;font-size:11px;letter-spacing:1.5px;color:#4A5664">ACCOUNT SECURITY</p>
<h1 style="margin:0 0 24px;font-size:25px;line-height:1.3">{escape(title)}</h1>
<p style="font-size:15px;line-height:1.7">Hello {escape(name)},</p>
<p style="font-size:15px;line-height:1.7">{escape(intro)}</p>
<p style="font-size:15px;line-height:1.7">{escape(instruction)}</p>{code_html}
<p style="font-size:13px;line-height:1.7;color:#4A5664">Never share your password or verification code.
Our team will never ask you to share either by email.</p>
{ignored_notice}
</td></tr><tr><td style="padding:20px 36px;border-top:1px solid #e6ece8;
font-size:12px;line-height:1.7;color:#4A5664">Aab o Hisaab · Secure account services<br>
This is an automated account notification.</td></tr>
</table></td></tr></table></body></html>"""
    return title, text, html


class AuthMailer:
    def ensure_configured(self):
        if not os.environ.get("SMTP_HOST") or not os.environ.get("SMTP_FROM_EMAIL"):
            raise MailDeliveryError("Email delivery is not configured.")

    def send(self, recipient, name, purpose, code=None):
        self.ensure_configured()
        subject, text, html = render_email(name, purpose, code)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((os.environ.get("SMTP_FROM_NAME", "Aab o Hisaab"), os.environ["SMTP_FROM_EMAIL"]))
        message["To"] = recipient
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        use_ssl = os.environ.get("SMTP_SECURITY", "starttls").lower() == "ssl"
        security = os.environ.get("SMTP_SECURITY", "starttls").lower()
        if security not in {"ssl", "starttls", "none"}:
            raise MailDeliveryError("Invalid SMTP security configuration.")
        client_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        kwargs = {"timeout": 10}
        if use_ssl:
            kwargs["context"] = ssl.create_default_context()
        try:
            with client_type(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), **kwargs) as client:
                if security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                username = os.environ.get("SMTP_USERNAME")
                if username:
                    client.login(username, os.environ.get("SMTP_PASSWORD", ""))
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailDeliveryError("Email delivery is temporarily unavailable.") from exc
