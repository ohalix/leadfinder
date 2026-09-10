from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict

logger = logging.getLogger(__name__)


class SMTPError(Exception):
    pass


def send_one(
    smtp_cfg: Dict,
    to: str,
    subject: str,
    body: str,
    html_body: bool = False,
) -> None:
    """
    Send a single email. Raises SMTPError on failure so the caller
    can log the error and continue sending to other recipients.

    Opens a fresh SMTP connection per message to avoid state issues
    in a multi-threaded context.
    """
    host = smtp_cfg.get("host", "")
    port = int(smtp_cfg.get("port", 587))
    username = smtp_cfg.get("username", "")
    password = smtp_cfg.get("password", "")
    from_addr = smtp_cfg.get("from_email") or username
    from_name = smtp_cfg.get("from_name", "LeadFinder")
    use_tls = smtp_cfg.get("use_tls", True)

    if not host or not username:
        raise SMTPError("SMTP host and username are required")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html" if html_body else "plain", "utf-8"))

    try:
        if use_tls:
            # STARTTLS on port 587 (recommended)
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(username, password)
                server.send_message(msg)
        else:
            # SSL on port 465
            with smtplib.SMTP_SSL(
                host, port, context=ssl.create_default_context(), timeout=30
            ) as server:
                server.login(username, password)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise SMTPError(f"SMTP authentication failed: {exc}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise SMTPError(f"Recipient refused by server: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise SMTPError(f"SMTP error: {exc}") from exc
    except OSError as exc:
        raise SMTPError(f"Network error connecting to {host}:{port}: {exc}") from exc
