"""
Email normalization.

Intentionally simple: lowercase, strip, light syntax check.
Full RFC 5322 compliance is not worth the false-negative rate for this use case.
"""
from __future__ import annotations

import re
from typing import Optional

# Minimal but sufficient syntax check
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

_JUNK_FRAGMENTS = frozenset({
    "example.com",
    "test@test.",
    "noreply@",
    "no-reply@",
    "donotreply@",
    "name@",
    "user@domain",
    "email@domain",
    "your@email",
    "yourname@",
    "@sentry.",
    "info@example",
    "@2x.",
    "@3x.",
    ".png@",
    ".jpg@",
})


def normalize_email(raw: str) -> Optional[str]:
    """
    Returns a normalised email string, or None if the input is invalid.

    Steps:
      1. Strip whitespace
      2. Remove leading mailto:
      3. Strip query string (mailto:a@b.com?subject=...)
      4. Lowercase
      5. Validate with light regex
    """
    if not raw:
        return None

    val = raw.strip()

    if val.lower().startswith("mailto:"):
        val = val[7:]

    # Strip query params that sometimes appear on mailto links
    val = val.split("?")[0].strip()

    val = val.lower()

    if not _EMAIL_RE.match(val):
        return None

    return val


def is_junk_email(normalized: str) -> bool:
    """Returns True for known placeholder / junk patterns after normalisation."""
    if not normalized:
        return True
    n = normalized.lower()
    return any(j in n for j in _JUNK_FRAGMENTS)
