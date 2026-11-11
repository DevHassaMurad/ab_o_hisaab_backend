"""Fund emails (request accepted / rejected, contribution received), styled like the account emails."""
import os
from html import escape


def _portal(path):
    return (os.environ.get("PORTAL_URL") or "http://localhost:5173").rstrip("/") + path


def render_notice(heading: str, label: str, greeting_name: str, intro: str, rows, link_path: str, link_text: str):
    link = _portal(link_path)
    text = f"Aab o Hisaab\n\n{heading}\n\nHello {greeting_name},\n\n{intro}\n\n"
    text += "".join(f"{k}: {v}\n" for k, v in rows)
    text += f"\n{link_text}: {link}\n"
    rows_html = "".join(
        f'<tr><td style="padding:8px 0;color:#4A5664;font-size:13px;width:160px">{escape(k)}</td>'
        f'<td style="padding:8px 0;font-size:14px;color:#121A24;font-weight:600">{escape(str(v))}</td></tr>'
        for k, v in rows
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F5F7;font-family:Arial,Helvetica,sans-serif;color:#121A24">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:40px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="max-width:560px;margin:auto;background:#fff;border:1px solid #dfe6e2;border-radius:12px">
<tr><td style="padding:28px 36px;border-bottom:1px solid #e6ece8;font-size:20px;font-weight:700;
color:#0F4C5C">Aab o Hisaab</td></tr>
<tr><td style="padding:32px 36px">
<p style="margin:0 0 12px;font-size:11px;letter-spacing:1.5px;color:#4A5664">{escape(label)}</p>
<h1 style="margin:0 0 24px;font-size:25px;line-height:1.3">{escape(heading)}</h1>
<p style="font-size:15px;line-height:1.7">Hello {escape(greeting_name)},</p>
<p style="font-size:15px;line-height:1.7">{escape(intro)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="margin:20px 0;border-top:1px solid #e6ece8;border-bottom:1px solid #e6ece8">{rows_html}</table>
<p style="margin:28px 0"><a href="{escape(link)}" style="display:inline-block;background:#0F4C5C;color:#fff;
text-decoration:none;padding:12px 22px;border-radius:6px;font-weight:600;font-size:14px">{escape(link_text)}</a></p>
</td></tr><tr><td style="padding:20px 36px;border-top:1px solid #e6ece8;
font-size:12px;line-height:1.7;color:#4A5664">Aab o Hisaab · PDMA fund services<br>
This is an automated notification.</td></tr>
</table></td></tr></table></body></html>"""
    return heading, text, html
