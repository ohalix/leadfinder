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

# Schema.org types whose itemprop contacts should be high-confidence
_HIGH_CONF_ITEM_TYPES = frozenset({
    "organization", "localbusiness", "contactpoint",
    "professionalservice", "legalservice", "financialservice",
    "foodestablishment", "hotel", "store", "service",
    "insuranceagency", "realestate", "autodealer",
    "medicalorganization", "educationalorganization",
})

# Meta tag name/property values that may contain phone or email
_META_PHONE_PROPS = frozenset({
    "telephone", "phone",
    "business:contact_data:phone_number",   # Facebook OG
    "og:phone_number",
    "twitter:phone_number",
})
_META_EMAIL_PROPS = frozenset({
    "email",
    "og:email",
    "business:contact_data:email",          # Facebook OG
})


def extract_structured(html: str, source_url: str) -> List[ExtractionHit]:
    """
    Tier 1 extraction — three structured data sources in priority order:
      1. JSON-LD (application/ld+json) — highest confidence
      2. HTML Microdata (itemprop attributes) — high confidence
      3. Meta tags (og:email, telephone meta, etc.) — medium confidence
    """
    hits: List[ExtractionHit] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        _extract_json_ld(soup, source_url, hits)
        _extract_microdata(soup, source_url, hits)
        _extract_meta(soup, source_url, hits)
    except Exception as exc:
        logger.debug(f"Structured extraction error on {source_url}: {exc}")
    return hits


# ── JSON-LD ──
def _extract_json_ld(
    soup: BeautifulSoup, source_url: str, hits: List[ExtractionHit]
) -> None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = (script.string or "").strip()
            if not raw:
                continue
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        _walk(data, source_url, hits)


def _walk(node: object, source_url: str, hits: List[ExtractionHit]) -> None:
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
        for key in ("email", "contactEmail"):
            val = node.get(key, "")
            if val and isinstance(val, str):
                raw = val.replace("mailto:", "").strip()
                if raw:
                    hits.append(ExtractionHit(
                        contact_type="email",
                        raw_value=raw,
                        normalized_value=raw,
                        method="schema",
                        confidence="high",
                        source_url=source_url,
                    ))

        for key in ("telephone", "faxNumber", "contactPhone"):
            val = node.get(key, "")
            if val and isinstance(val, str):
                raw = val.replace("tel:", "").strip()
                if raw:
                    hits.append(ExtractionHit(
                        contact_type="phone",
                        raw_value=raw,
                        normalized_value=raw,
                        method="schema",
                        confidence="high",
                        source_url=source_url,
                    ))

    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, source_url, hits)


# ── HTML Microdata ──
def _extract_microdata(
    soup: BeautifulSoup, source_url: str, hits: List[ExtractionHit]
) -> None:
    """
    Parse HTML Microdata (itemprop="telephone" / itemprop="email").

    Confidence is 'high' when the element is inside a known Organization
    or LocalBusiness itemscope; 'medium' otherwise.

    Examples caught:
        <span itemprop="telephone">+1 555 123 4567</span>
        <a itemprop="email" href="mailto:info@co.com">info@co.com</a>
        <meta itemprop="telephone" content="+15551234567">
    """
    # Build a map from element → nearest ancestor itemtype for confidence scoring
    itemscope_types: dict = {}
    for el in soup.find_all(attrs={"itemscope": True}):
        raw_type = (el.get("itemtype") or "").lower()
        for known in _HIGH_CONF_ITEM_TYPES:
            if known in raw_type:
                itemscope_types[id(el)] = "high"
                break
        else:
            itemscope_types[id(el)] = "medium"

    def _confidence_for(el) -> str:
        """Walk ancestors to find the nearest itemscope's confidence."""
        for parent in el.parents:
            if id(parent) in itemscope_types:
                return itemscope_types[id(parent)]
        return "medium"

    # telephone
    for el in soup.find_all(attrs={"itemprop": "telephone"}):
        raw = (
            el.get("content")          # <meta itemprop="telephone" content="...">
            or el.get("href", "").replace("tel:", "")
            or el.get_text(strip=True)
        ).strip()
        if raw:
            hits.append(ExtractionHit(
                contact_type="phone",
                raw_value=raw,
                normalized_value=raw,
                method="microdata",
                confidence=_confidence_for(el),
                source_url=source_url,
            ))

    # email
    for el in soup.find_all(attrs={"itemprop": "email"}):
        raw = (
            el.get("content")
            or el.get("href", "").replace("mailto:", "").split("?")[0]
            or el.get_text(strip=True)
        ).strip()
        if raw:
            hits.append(ExtractionHit(
                contact_type="email",
                raw_value=raw,
                normalized_value=raw,
                method="microdata",
                confidence=_confidence_for(el),
                source_url=source_url,
            ))


# ── Meta tags ──
def _extract_meta(
    soup: BeautifulSoup, source_url: str, hits: List[ExtractionHit]
) -> None:
    """
    Parse <meta> tags that may carry phone/email contact data.

    Covers:
      - <meta name="telephone" content="...">
      - <meta property="og:email" content="...">
      - <meta property="business:contact_data:phone_number" content="...">
      - <meta property="business:contact_data:email" content="...">
    """
    for meta in soup.find_all("meta"):
        key = (meta.get("name") or meta.get("property") or "").lower().strip()
        content = (meta.get("content") or "").strip()
        if not key or not content:
            continue

        if key in _META_PHONE_PROPS:
            hits.append(ExtractionHit(
                contact_type="phone",
                raw_value=content,
                normalized_value=content,
                method="meta",
                confidence="medium",
                source_url=source_url,
            ))
        elif key in _META_EMAIL_PROPS:
            raw = content.replace("mailto:", "").split("?")[0].strip()
            if raw:
                hits.append(ExtractionHit(
                    contact_type="email",
                    raw_value=raw,
                    normalized_value=raw,
                    method="meta",
                    confidence="medium",
                    source_url=source_url,
                ))
