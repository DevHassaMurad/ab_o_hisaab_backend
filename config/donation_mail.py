"""Donation request email, styled like the account emails in config/auth_mail.py."""
import os
from html import escape


def money(value):
    return f"PKR {value:,.2f}"


def render_donation_request(donor_name, request, requested_by_name):
    portal = (os.environ.get("PORTAL_URL") or "http://localhost:5173").rstrip("/")
    link = f"{portal}/donation-requests/{request.id}"
    title = f"Donation request {request.document_no}"
    amount = money(request.requested_amount)
    text = (
        f"Aab o Hisaab\n\n{title}\n\nHello {donor_name},\n\n"
        f"PDMA has sent you a donation request.\n\n"
        f"Reference: {request.document_no}\n"
        f"Reason: {request.reason}\n"
        f"Amount requested: {amount}\n"
        f"Requested by: {requested_by_name}\n\n"
        f"Open your donor dashboard to review it: {link}\n"
    )
    rows = "".join(
        f'<tr><td style="padding:8px 0;color:#4A5664;font-size:13px;width:150px">{escape(k)}</td>'
        f'<td style="padding:8px 0;font-size:14px;color:#121A24;font-weight:600">{escape(v)}</td></tr>'
        for k, v in (
            ("Reference", request.document_no),
            ("Reason", request.reason),
            ("Amount requested", amount),
            ("Requested by", requested_by_name),
        )
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F5F7;font-family:Arial,Helvetica,sans-serif;color:#121A24">
<div style="display:none;max-height:0;overflow:hidden">{escape(title)} — {escape(amount)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:40px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="max-width:560px;margin:auto;background:#fff;border:1px solid #dfe6e2;border-radius:12px">
<tr><td style="padding:28px 36px;border-bottom:1px solid #e6ece8;font-size:20px;font-weight:700;
color:#0F4C5C">Aab o Hisaab</td></tr>
<tr><td style="padding:32px 36px">
<p style="margin:0 0 12px;font-size:11px;letter-spacing:1.5px;color:#4A5664">DONATION REQUEST</p>
<h1 style="margin:0 0 24px;font-size:25px;line-height:1.3">{escape(title)}</h1>
<p style="font-size:15px;line-height:1.7">Hello {escape(donor_name)},</p>
<p style="font-size:15px;line-height:1.7">PDMA has sent you a donation request. The details are below.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="margin:20px 0;border-top:1px solid #e6ece8;border-bottom:1px solid #e6ece8">{rows}</table>
<p style="margin:28px 0"><a href="{escape(link)}" style="display:inline-block;background:#0F4C5C;color:#fff;
text-decoration:none;padding:12px 22px;border-radius:6px;font-weight:600;font-size:14px">Review the request</a></p>
<p style="font-size:13px;line-height:1.7;color:#4A5664">Every rupee is tracked against this reference
from receipt to the NGO that spends it.</p>
</td></tr><tr><td style="padding:20px 36px;border-top:1px solid #e6ece8;
font-size:12px;line-height:1.7;color:#4A5664">Aab o Hisaab · Donor services<br>
This is an automated notification.</td></tr>
</table></td></tr></table></body></html>"""
    return title, text, html
