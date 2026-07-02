from __future__ import annotations
import logging
from typing import Dict, List, Tuple
from app.extraction.structured import extract_structured
from app.extraction.patterns import extract_patterns
from app.extraction.confidence import score_hits, site_domain
from app.models import ExtractionHit

logger = logging.getLogger(__name__)
_METHOD_PRIORITY: Dict[str, int] = {
    "schema": 5,
    "mailto": 4,
    "tel":    4,
    "footer": 3,
    "text":   2,
    "playwright": 1
}


def extract_contacts(html: str, source_url: str) -> List[ExtractionHit]:
    if not html:
        return []

    sdom = site_domain(source_url)
    structured_hits = extract_structured(html, source_url)
    pattern_hits    = extract_patterns(html, source_url)

    all_hits = structured_hits + pattern_hits
    deduped  = _dedup(all_hits)
    scored   = score_hits(deduped, sdom)

    logger.debug(
        f"extract_contacts({source_url}): structured={len(structured_hits):,d} patterns={len(pattern_hits):,d} → deduped={len(deduped):,d} scored={len(scored):,d}")
    return scored

def _dedup(hits: List[ExtractionHit]) -> List[ExtractionHit]:
    best: Dict[Tuple[str, str], ExtractionHit] = {}
    for hit in hits:
        key = (hit.contact_type, hit.raw_value.lower().strip())
        if key not in best:
            best[key] = hit
        else:
            if _METHOD_PRIORITY.get(hit.method, 0) > _METHOD_PRIORITY.get(best[key].method, 0):
                best[key] = hit

    return list(best.values())
