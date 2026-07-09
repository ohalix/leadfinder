"""
Tiers 2, 3, 4 — DOM-level and text-level contact extraction.

Priority order within this module:
  Tier 2  — mailto: and tel: href links                    → confidence: high
  Tier 2b — data-phone / data-email / data-tel attributes  → confidence: high
  Tier 3  — semantic DOM zone selectors                    → confidence: medium
  Tier 4  — full body-text regex fallback                  → confidence: low

Junk filters are applied inline so no noise reaches the normalizer.
"""
from __future__ import annotations
import logging
import re
from typing import List, Set
from bs4 import BeautifulSoup
from app.models import ExtractionHit

logger = logging.getLogger(__name__)

# ── Regex patterns ──
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Phone: covers US formats, international with +, extensions
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?"           # optional country code
    r"(?:\(?\d{2,4}\)?[\s.\-]?)"          # area / city code
    r"\d{3,4}[\s.\-]?\d{3,4}"             # main digits
    r"(?:[\s]?(?:ext|x|#)[\s]?\d{1,5})?", # optional extension
    re.IGNORECASE,
)

# Quick-reject patterns
_JUNK_EMAIL = re.compile(
    r"(example\.com|test@test\.|noreply@|no-reply@"
    r"|@sentry\.|name@domain|user@domain|email@domain"
    r"|your@email|info@example|yourname@"
    r"|\.png|\.jpg|\.gif|\.svg|\.webp|@2x|@3x)",
    re.IGNORECASE,
)

_JUNK_PHONE = re.compile(
    r"^(\+?0+|1?[2-9]\d{2}555\d{4}|000)"  # 555 fakes, all-zeros
)

# ── Tier 3 zone selectors ──
# Ordered from highest-signal to lower to aid the seen_texts dedup:
# the first selector that captures a zone's text wins.

_ZONE_SELECTORS = [
    # ── HTML5 semantic elements ──
    "footer",
    "header",
    "address",          # spec-defined contact info container
    "aside",

    # ── ARIA landmark roles ──
    "[role='contentinfo']",    # WAI-ARIA semantic footer
    "[role='complementary']",  # WAI-ARIA sidebar

    # ── Explicit IDs ──
    "#contact", "#contact-us", "#get-in-touch", "#reach-us",

    # ── Contact class/id patterns (existing) ──
    ".contact",
    "[class*='contact']", "[id*='contact']",

    # ── Footer/header patterns (existing) ──
    "[class*='footer']", "[id*='footer']",
    "[class*='header']", "[id*='header']",

    # ── Reach / touch / info (existing) ──
    "[class*='reach']", "[class*='touch']",
    "[class*='info']",  "[id*='info']",

    # ── Address and location blocks (new) ──
    "[class*='address']", "[id*='address']",
    "[class*='location']", "[id*='location']",

    # ── Phone-specific containers (new) ──
    "[class*='phone']", "[id*='phone']",
    "[class*='tel']",   "[id*='tel']",

    # ── Email-specific containers (new) ──
    "[class*='email']", "[id*='email']",

    # ── vCard / hCard microformat classes (new) ──
    ".vcard", ".h-card", ".hcard",

    # ── About sections (often contain contact info) (new) ──
    "[class*='about']", "[id*='about']",
]


# ── Public API ──
def extract_patterns(html: str, source_url: str) -> List[ExtractionHit]:
    """Run tiers 2, 2b, 3, 4 and return all hits (deduplicated within this call)."""
    hits: List[ExtractionHit] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        _tier2_links(soup, source_url, hits)
        _tier2b_data_attrs(soup, source_url, hits)
        _tier3_zones(soup, source_url, hits)
        _tier4_body(soup, source_url, hits)
    except Exception as exc:
        logger.debug(f"Pattern extraction error on {source_url}: {exc}")
    return hits

# ── Tier 2 — explicit href links ──
def _tier2_links(
    soup: BeautifulSoup, source_url: str, hits: List[ExtractionHit]
) -> None:
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()

        if href.lower().startswith("mailto:"):
            raw = href[7:].split("?")[0].strip()
            if raw and not _JUNK_EMAIL.search(raw):
                hits.append(ExtractionHit(
                    contact_type="email",
                    raw_value=raw,
                    normalized_value=raw,
                    method="mailto",
                    confidence="high",
                    source_url=source_url,
                ))

        elif href.lower().startswith("tel:"):
            raw = href[4:].strip()
            if raw and not _JUNK_PHONE.match(raw):
                hits.append(ExtractionHit(
                    contact_type="phone",
                    raw_value=raw,
                    normalized_value=raw,
                    method="tel",
                    confidence="high",
                    source_url=source_url,
                ))


# ── Tier 2b — data-attribute contact fields ──
# Modern React/Vue SPAs often encode phone numbers in data attributes on
# clickable divs/spans/buttons instead of <a href="tel:"> anchors.
# Common patterns: data-phone, data-tel, data-contact-phone, data-email.

_DATA_PHONE_ATTRS = ("data-phone", "data-tel", "data-telephone",
                     "data-contact-phone", "data-mobile")
_DATA_EMAIL_ATTRS = ("data-email", "data-contact-email", "data-mail")


def _tier2b_data_attrs(
    soup: BeautifulSoup, source_url: str, hits: List[ExtractionHit]
) -> None:
    # Collect all tags that carry any of the target data attributes
    for tag in soup.find_all(True):
        for attr in _DATA_PHONE_ATTRS:
            raw = (tag.get(attr) or "").strip()
            if raw and not _JUNK_PHONE.match(raw):
                hits.append(ExtractionHit(
                    contact_type="phone",
                    raw_value=raw,
                    normalized_value=raw,
                    method="tel",
                    confidence="high",
                    source_url=source_url,
                ))
                break  # only the first matching phone attr per element

        for attr in _DATA_EMAIL_ATTRS:
            raw = (tag.get(attr) or "").replace("mailto:", "").split("?")[0].strip()
            if raw and not _JUNK_EMAIL.search(raw):
                hits.append(ExtractionHit(
                    contact_type="email",
                    raw_value=raw,
                    normalized_value=raw,
                    method="mailto",
                    confidence="high",
                    source_url=source_url,
                ))
                break


# ── Tier 3 — semantic DOM zones ──
def _tier3_zones(
    soup: BeautifulSoup, source_url: str, hits: List[ExtractionHit]
) -> None:
    seen_texts: Set[str] = set()

    for selector in _ZONE_SELECTORS:
        try:
            elements = soup.select(selector)
        except Exception:
            continue

        for el in elements:
            text = el.get_text(separator=" ", strip=True)
            if not text or text in seen_texts or len(text) < 5:
                continue
            seen_texts.add(text)

            for match in EMAIL_RE.findall(text):
                if not _JUNK_EMAIL.search(match):
                    hits.append(ExtractionHit(
                        contact_type="email",
                        raw_value=match,
                        normalized_value=match,
                        method="footer",
                        confidence="medium",
                        source_url=source_url,
                    ))

            for match in PHONE_RE.findall(text):
                clean = match.strip()
                if clean and not _JUNK_PHONE.match(clean) and len(clean) >= 7:
                    hits.append(ExtractionHit(
                        contact_type="phone",
                        raw_value=clean,
                        normalized_value=clean,
                        method="footer",
                        confidence="medium",
                        source_url=source_url,
                    ))

# ── Tier 4 — full body text ──
def _tier4_body(
    soup: BeautifulSoup, source_url: str, hits: List[ExtractionHit]
) -> None:
    # Remove noise tags before extracting text
    for tag in soup(["script", "style", "meta", "link", "noscript", "head"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)

    for match in EMAIL_RE.findall(text):
        if not _JUNK_EMAIL.search(match):
            hits.append(ExtractionHit(
                contact_type="email",
                raw_value=match,
                normalized_value=match,
                method="text",
                confidence="low",
                source_url=source_url,
            ))

    for match in PHONE_RE.findall(text):
        clean = match.strip()
        if clean and not _JUNK_PHONE.match(clean) and len(clean) >= 7:
            hits.append(ExtractionHit(
                contact_type="phone",
                raw_value=clean,
                normalized_value=clean,
                method="text",
                confidence="low",
                source_url=source_url,
            ))
