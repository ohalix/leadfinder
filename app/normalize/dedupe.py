from __future__ import annotations
from typing import Dict, List, Tuple
from app.models import ExtractionHit

_CONF_ORD = {"high": 3, "medium": 2, "low": 1}


def dedup_hits(hits: List[ExtractionHit]) -> List[ExtractionHit]:
    best: Dict[Tuple[str, str], ExtractionHit] = {}

    for hit in hits:
        # Lowercase the key so "A@B.COM" and "a@b.com" collapse to the same bucket.
        # The canonical normalized_value on the winning hit is preserved as-is.
        key = (hit.contact_type, hit.normalized_value.lower())
        if key not in best:
            best[key] = hit
        else:
            existing_conf = _CONF_ORD.get(best[key].confidence, 0)
            new_conf      = _CONF_ORD.get(hit.confidence, 0)
            if new_conf > existing_conf:
                best[key] = hit

    return list(best.values())
