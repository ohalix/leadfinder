"""
Tier 1 — Schema.org / JSON-LD structured data extraction.

Sites embed this specifically to be machine-read, making it the highest-
confidence, lowest-noise source.  Checked before any DOM or regex work.

Walks nested JSON-LD graphs looking for:
  - email / contactEmail fields
  - telephone / faxNumber fields

on nodes whose @type is any recognised contact-bearing schema.
"""
from __future__ import annotations

import json
import logging
from typing import List

from bs4 import BeautifulSoup

from app.models import ExtractionHit

logger = logging.getLogger(__name__)

_CONTACT_TYPES = frozenset({
    "Organization", "LocalBusiness", "ContactPoint",
    "Person", "MedicalOrganization", "EducationalOrganization",
    "FoodEstablishment", "Hotel", "Store", "Service",
    "ProfessionalService", "LegalService", "FinancialService",
    "InsuranceAgency", "RealEstateAgent", "AutoDealer",
})


def extract_structured(html: str, source_url: str) -> List[ExtractionHit]:
    """Parse all JSON-LD blocks and return high-confidence hits."""
    hits: List[ExtractionHit] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                raw = (script.string or "").strip()
                if not raw:
                    continue
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            _walk(data, source_url, hits)
    except Exception as exc:
        logger.debug("Structured extraction error on %s: %s", source_url, exc)
    return hits


def _walk(node: object, source_url: str, hits: List[ExtractionHit]) -> None:
    """Recursively traverse JSON-LD graph."""
    if isinstance(node, list):
        for item in node:
            _walk(item, source_url, hits)
        return
    if not isinstance(node, dict):
        return

    node_type = node.get("@type", "")
    types = node_type if isinstance(node_type, list) else [node_type]
    is_contact_node = any(t in _CONTACT_TYPES for t in types)

    if is_contact_node:
        # Emails
        for key in ("email", "contactEmail"):
            val = node.get(key, "")
            if val and isinstance(val, str):
                raw = val.replace("mailto:", "").strip()
                if raw:
                    hits.append(ExtractionHit(
                        contact_type="email",
                        raw_value=raw,
                        normalized_value=raw,   # normalized later
                        method="schema",
                        confidence="high",
                        source_url=source_url,
                    ))

        # Phones
        for key in ("telephone", "faxNumber", "contactPhone"):
            val = node.get(key, "")
            if val and isinstance(val, str):
                raw = val.replace("tel:", "").strip()
                if raw:
                    hits.append(ExtractionHit(
                        contact_type="phone",
                        raw_value=raw,
                        normalized_value=raw,   # normalized later
                        method="schema",
                        confidence="high",
                        source_url=source_url,
                    ))

    # Recurse into all nested values
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, source_url, hits)
