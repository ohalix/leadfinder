from __future__ import annotations

from typing import List
from urllib.parse import urlparse

from app.models import ExtractionHit

_FREEMAIL = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "protonmail.com",
        "proton.me",
        "aol.com",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "live.com",
        "msn.com",
        "me.com",
    }
)
_ORD = {"high": 3, "medium": 2, "low": 1}
_LBL = {3: "high", 2: "medium", 1: "low"}


def score_hits(hits: List[ExtractionHit], site_domain: str) -> List[ExtractionHit]:
    for hit in hits:
        level = _ORD.get(hit.confidence, 1)
        if hit.contact_type == "email":
            email_domain = _email_domain(hit.raw_value)
            if email_domain and site_domain and email_domain == site_domain:
                level = min(level + 1, 3)
            elif email_domain in _FREEMAIL:
                level = max(level - 1, 1)

        hit.confidence = _LBL.get(level, "low")

    return hits


def site_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _email_domain(email: str) -> str:
    parts = email.rsplit("@", 1)
    return parts[1].lower() if len(parts) == 2 else ""
