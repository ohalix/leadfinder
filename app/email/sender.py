from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List

from app.email.smtp import SMTPError, send_one
from app.storage import repository

logger = logging.getLogger(__name__)


def send_leads_email(
    smtp_cfg: Dict[str, Any],
    leads: List[Dict[str, Any]],
    subject_tpl: str,
    body_tpl: str,
    rate_delay: float = 1.0,
    html_body: bool = False,
) -> List[Dict[str, Any]]:
    """
    Send emails to a list of lead dicts and record each outcome.

    Returns a list of result dicts:
        [{"email": "...", "status": "sent"|"failed", "error": "..."|None}, ...]
    """
    run_label = str(uuid.uuid4())[:8]  # short ID to group this send batch
    results: List[Dict[str, Any]] = []

    email_leads = [
        l
        for l in leads
        if l.get("contact_type") == "email" and l.get("normalized_value")
    ]

    if not email_leads:
        logger.warning("send_leads_email: no email-type leads in the provided list")
        return []

    logger.info(
        f"Email send [{run_label}]: {len(email_leads)} recipient(s), "
        f"rate_delay={rate_delay}s"
    )

    for lead in email_leads:
        recipient = lead["normalized_value"]
        domain = lead.get("domain", "")
        query = (lead.get("queries") or [""])[0]
        lead_id = lead.get("id")

        try:
            subject = subject_tpl.format(email=recipient, domain=domain, query=query)
            body = body_tpl.format(email=recipient, domain=domain, query=query)
        except KeyError as exc:
            error_msg = f"Template variable not found: {exc}"
            logger.warning(f"[{run_label}] Template error for {recipient}: {error_msg}")
            results.append({"email": recipient, "status": "failed", "error": error_msg})
            repository.record_email_send(
                lead_id=lead_id,
                recipient=recipient,
                subject=subject_tpl,
                status="failed",
                error=error_msg,
                run_label=run_label,
            )
            continue

        try:
            send_one(smtp_cfg, recipient, subject, body, html_=html_body)
            logger.info(f"[{run_label}] Sent → {recipient}")
            results.append({"email": recipient, "status": "sent", "error": None})
            repository.record_email_send(
                lead_id=lead_id,
                recipient=recipient,
                subject=subject,
                status="sent",
                run_label=run_label,
            )
        except SMTPError as exc:
            error_msg = str(exc)
            logger.warning(f"[{run_label}] Failed → {recipient}: {error_msg}")
            results.append({"email": recipient, "status": "failed", "error": error_msg})
            repository.record_email_send(
                lead_id=lead_id,
                recipient=recipient,
                subject=subject,
                status="failed",
                error=error_msg,
                run_label=run_label,
            )

        if rate_delay > 0:
            time.sleep(rate_delay)

    sent = sum(1 for r in results if r["status"] == "sent")
    failed = sum(1 for r in results if r["status"] == "failed")
    logger.info(f"Email send [{run_label}] complete: {sent} sent, {failed} failed")

    return results
