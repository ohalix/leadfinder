from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Two tiers: primary (strong contact signals) and secondary (weaker signals).
# Path match against primary scores +4; secondary scores +2.
# Text match adds +2 for primary, +1 for secondary.
_CONTACT_PRIMARY_RE = re.compile(
    r"\b(contact|reach[\s\-]*us|get[\s\-]*in[\s\-]*touch|enquir|inquir|hello)\b",
    re.IGNORECASE,
)
_CONTACT_SECONDARY_RE = re.compile(
    r"\b(about[\s\-]*us|about|support)\b",
    re.IGNORECASE,
)


def find_contact_page(html: str, base_url: str) -> Optional[str]:
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
        base_domain = urlparse(base_url).netloc.lower()
        candidates: List[Tuple[int, str]] = []  # (score, url)

        for a_tag in soup.find_all("a", href=True):
            href = (a_tag.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue

            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            # Same domain only
            link_domain = parsed.netloc.lower()
            if link_domain and link_domain != base_domain:
                continue

            # Reject non-HTML extensions
            path = parsed.path.lower()
            if any(
                path.endswith(ext)
                for ext in (
                    ".pdf",
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".gif",
                    ".svg",
                    ".zip",
                    ".doc",
                    ".docx",
                    ".xls",
                    ".xlsx",
                )
            ):
                continue

            anchor_text = (a_tag.get_text(separator=" ", strip=True) or "").lower()
            score = 0
            if _CONTACT_PRIMARY_RE.search(path):
                score += 4  # strong path signal (contact, reach-us …)
            elif _CONTACT_SECONDARY_RE.search(path):
                score += 2  # weaker path signal (about, support …)
            if _CONTACT_PRIMARY_RE.search(anchor_text):
                score += 2  # strong text signal
            elif _CONTACT_SECONDARY_RE.search(anchor_text):
                score += 1  # weaker text signal
            # Prefer short paths (/contact, /contact-us) over deeply nested ones
            depth = len([p for p in path.split("/") if p])
            if depth <= 2:
                score += 1

            if score > 0:
                candidates.append((score, full_url))

        if not candidates:
            return None

        candidates.sort(key=lambda t: t[0], reverse=True)
        best = candidates[0][1]
        logger.debug(f"Contact page candidate: {best} (score={candidates[0][0]})")
        return best
    except Exception as exc:
        logger.debug(f"Contact page discovery error on {base_url}: {exc}")
        return None
