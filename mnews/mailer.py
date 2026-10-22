"""Envoi d'emails via le SMTP o2switch."""
from __future__ import annotations

import html
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from mnews.config import settings

log = logging.getLogger("mnews.mailer")

_URL_RE = re.compile(r"(https?://[^\s<>\"]+)")


def text_to_html(body: str) -> str:
    """Convertit le texte brut en HTML minimal : liens cliquables, sauts de ligne."""
    lines = []
    for line in body.splitlines():
        escaped = html.escape(line)
        escaped = _URL_RE.sub(r'<a href="\1">\1</a>', escaped)
        lines.append(escaped if escaped else "&nbsp;")
    joined = "<br>\n".join(lines)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"></head>"
        "<body style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "font-size:15px;line-height:1.5;color:#111;max-width:720px;margin:0 auto;padding:16px\">"
        f"{joined}</body></html>"
    )


def send_email(subject: str, body_text: str, to: list[str], html_alternative: bool = True) -> str:
    settings.check_required("smtp_host", "smtp_user", "smtp_password", "mail_from")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.mail_from_name, settings.mail_from))
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.mail_from.split("@")[-1])
    msg.set_content(body_text, charset="utf-8")
    if html_alternative:
        msg.add_alternative(text_to_html(body_text), subtype="html", charset="utf-8")

    if settings.smtp_ssl:
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=30)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    with server:
        if not settings.smtp_ssl:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    log.info("Email envoyé à %s : %s", ", ".join(to), subject)
    return msg["Message-ID"]


def send_alert(subject: str, body: str) -> None:
    """Email d'alerte en cas d'échec du pipeline ; ne doit jamais lever d'exception."""
    try:
        send_email(f"[News IA] ALERTE : {subject}", body, settings.alert_recipients(),
                   html_alternative=False)
    except Exception as exc:  # noqa: BLE001
        log.error("Impossible d'envoyer l'alerte : %s", exc)
