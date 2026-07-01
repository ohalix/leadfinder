"""
Extraction orchestrator.

Runs the four-tier pipeline for a single HTML document:
  1. Schema.org / JSON-LD   (structured.py)
  2. mailto: / tel: links   (patterns.py – tier 2)
  3. Footer/header zones    (patterns.py – tier 3)
  4. Body-text regex        (patterns.py – tier 4)

All tiers run; within-page duplicates are collapsed by keeping the highest-
priority method for each (contact_type, raw_value) pair.
Confidence is then re-scored against the site's own domain.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from app.extraction.structured import extract_structured
from app.extraction.patterns import extract_patterns
from app.extraction.confidence import score_hits, site_domain
from app.models import ExtractionHit

logger = logging.getLogger(__name__)

# Higher number = wins dedup
_METHOD_PRIORITY: Dict[str, int] = {
    "schema": 5,
    "mailto": 4,
    "tel":    4,
    "footer": 3,
    "text":   2,
    "playwright": 1,  # explicit method tag for playwright-derived hits
}


def extract_contacts(html: str, source_url: str) -> List[ExtractionHit]:
    """
    Full extraction pipeline for a single page.
    Returns a deduplicated, confidence-scored list of ExtractionHit objects.
    """
    if not html:
        return []

    sdom = site_domain(source_url)

    structured_hits = extract_structured(html, source_url)
    pattern_hits    = extract_patterns(html, source_url)

    all_hits = structured_hits + pattern_hits
    deduped  = _dedup(all_hits)
    scored   = score_hits(deduped, sdom)

    logger.debug(
        "extract_contacts(%s): structured=%d patterns=%d → deduped=%d scored=%d",
        source_url,
        len(structured_hits),
        len(pattern_hits),
        len(deduped),
        len(scored),
    )
    return scored


# ── Private ───────────────────────────────────────────────────────────────────

def _dedup(hits: List[ExtractionHit]) -> List[ExtractionHit]:
    """
    Collapse same (contact_type, raw_value_lowercase) pairs,
    keeping the hit with the highest-priority method.
    """
    best: Dict[Tuple[str, str], ExtractionHit] = {}

    for hit in hits:
        key = (hit.contact_type, hit.raw_value.lower().strip())
        if key not in best:
            best[key] = hit
        else:
            if _METHOD_PRIORITY.get(hit.method, 0) > _METHOD_PRIORITY.get(best[key].method, 0):
                best[key] = hit

    return list(best.values())
