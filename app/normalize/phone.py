"""
Phone number normalization.

Uses Google's `phonenumbers` library (libphonenumber port) for correct
E.164 formatting and validity checking.

A configurable default_region is used when no country code prefix is present
in the raw number (most common for US/CA numbers written without +1).

Degrades gracefully if the library is somehow missing (returns raw string
after basic sanity checks).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import phonenumbers
    from phonenumbers import NumberParseException, PhoneNumberFormat
    _LIB_OK = True
except ImportError:
    logger.warning(
        "phonenumbers not installed — phone normalisation will be limited."
    )
    _LIB_OK = False

# Pre-filter: reject obviously non-numeric or too-short strings early
_DIGITS_RE = re.compile(r"\d")


def normalize_phone(raw: str, default_region: str = "US") -> Optional[str]:
    """
    Returns an E.164-formatted phone number string, or None if invalid.

    A return of None means: this string is not a real phone number and
    should be silently discarded.
    """
    if not raw:
        return None

    val = raw.strip()

    if not _LIB_OK:
        # Minimal fallback: at least 7 digits present
        return val if len(_DIGITS_RE.findall(val)) >= 7 else None

    try:
        parsed = phonenumbers.parse(val, default_region)
    except NumberParseException:
        return None
    except Exception as exc:
        logger.debug("Unexpected phone parse error for %r: %s", val, exc)
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def is_junk_phone(raw: str) -> bool:
    """
    Quick pre-filter before handing off to the phonenumbers library.
    Catches obvious non-phones that regex may have matched (dates, ISBNs, etc.)
    """
    if not raw:
        return True

    stripped = re.sub(r"[\s\-().+]", "", raw)

    # Must have between 7 and 15 digits
    digits = re.sub(r"\D", "", stripped)
    if len(digits) < 7 or len(digits) > 15:
        return True

    # All-same digit (000000, 1111111, etc.)
    if len(set(digits)) <= 1:
        return True

    # Obvious fake (555-xxxx US fiction number)
    if re.match(r"^1?[2-9]\d{2}555\d{4}$", digits):
        return True

    return False
