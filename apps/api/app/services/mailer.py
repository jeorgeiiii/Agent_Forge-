"""Outgoing email. MAIL_BACKEND = console (default: log only) | smtp.

The console backend also keeps the last messages in memory (``OUTBOX``) so tests
and local development can see what would have been sent.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections import deque
from email.message import EmailMessage

from starlette.concurrency import run_in_threadpool

from ..config import get_settings

log = logging.getLogger("agentforge.mail")
OUTBOX: deque[dict[str, str]] = deque(maxlen=100)


class MailError(RuntimeError):
    pass


def _send_smtp(message: EmailMessage) -> None:
    settings = get_settings()
    context = ssl.create_default_context()
    if settings.smtp_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=15) as smtp:
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=context)
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_email(to: str, subject: str, text: str) -> bool:
    """Send a plain-text email. Returns True if handed to a real mail server."""
    settings = get_settings()
    if settings.mail_backend != "smtp":
        OUTBOX.append({"to": to, "subject": subject, "text": text})
        log.info("EMAIL (console backend) to=%s subject=%s\n%s", to, subject, text)
        return False
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    try:
        await run_in_threadpool(_send_smtp, message)
    except (OSError, smtplib.SMTPException) as error:
        raise MailError(f"Could not send email: {error}") from error
    return True
