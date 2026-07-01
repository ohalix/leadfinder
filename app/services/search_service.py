"""
Search service — orchestrates the full pipeline:

  1. SERP query via SerpAPI
  2. Domain denylist filter
  3. robots.txt check + HTTP fetch (per URL)
  4. Playwright fallback if JS shell detected
  5. Tier 1–4 contact extraction
  6. One same-domain contact-page hop if yield is low
  7. Normalization + junk filtering
  8. Cross-page deduplication
  9. Upsert into SQLite (seen_count tracking)
 10. Return structured response with leads + run summary

Routes call `run_search()` and nothing else from this layer.

NOTE: `config` is Flask's app.config dict-like object — always use
      config.get("KEY", default) or config["KEY"], never config.KEY.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.models import ExtractionHit, FetchOutcome, SerpResult
from app.scraper.fetcher import fetch_page
from app.scraper.discovery import find_contact_page
from app.scraper.playwright_renderer import is_js_shell, render_page
from app.extraction.extractor import extract_contacts
from app.normalize.email import normalize_email, is_junk_email
from app.normalize.phone import normalize_phone, is_junk_phone
from app.normalize.dedupe import dedup_hits
from app.serp.client import get_serp_client, SerpAPIError
from app.storage import repository

logger = logging.getLogger(__name__)


def run_search(
    query: str,
    config: Any,
    max_results: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Main entry point.  Returns a JSON-serialisable dict:
      {run_id, query, source_engine, leads: [...], summary: {...}}

    `config` is current_app.config — a Flask dict.  Use config.get() everywhere.
    """
    run_id        = str(uuid.uuid4())
    max_results   = max_results or config.get("SERP_MAX_RESULTS", 10)
    source_engine = config.get("SERP_PROVIDER", "serpapi")
    denylist      = config.get("DOMAIN_DENYLIST", frozenset())
    api_key       = config.get("SERP_API_KEY", "")
    user_agent    = config.get("USER_AGENT", "LeadFinderBot/0.1")
    timeout       = config.get("REQUEST_TIMEOUT", 10)
    rate_delay    = config.get("RATE_LIMIT_DELAY", 1.0)
    pw_enabled    = config.get("PLAYWRIGHT_ENABLED", False)
    pw_timeout    = config.get("PLAYWRIGHT_TIMEOUT", 15000)
    phone_region  = config.get("DEFAULT_PHONE_REGION", "US")

    repository.create_run(run_id, query, source_engine)
    logger.info("[%s] Search started: %r  max=%d", run_id, query, max_results)

    # ── 1. SERP ───────────────────────────────────────────────────────────────
    try:
        client = get_serp_client(source_engine, api_key)
        serp_results: List[SerpResult] = client.search(query, max_results=max_results)
    except (SerpAPIError, ValueError) as exc:
        logger.error("[%s] SERP failed: %s", run_id, exc)
        repository.complete_run(run_id, 0, 0, "failed")
        return _error_response(run_id, str(exc))

    logger.info("[%s] SERP returned %d result(s)", run_id, len(serp_results))

    # ── 2. Denylist filter ────────────────────────────────────────────────────
    allowed: List[SerpResult] = []
    skipped_deny = 0
    for r in serp_results:
        if _is_denylisted(r.domain, denylist):
            skipped_deny += 1
            repository.insert_serp_result(run_id, r, "skipped:denylisted", None)
        else:
            allowed.append(r)

    logger.info(
        "[%s] After denylist: %d allowed, %d skipped",
        run_id, len(allowed), skipped_deny,
    )

    # ── 3–6. Fetch / extract loop ─────────────────────────────────────────────
    all_hits: List[ExtractionHit] = []
    outcomes: List[FetchOutcome]  = []
    seen_urls: set                = set()
    pages_fetched = pages_skipped = pages_failed = 0

    for result in allowed:
        url = result.url
        if url in seen_urls:
            continue
        seen_urls.add(url)

        dom = result.domain or _domain(url)

        # Fetch main page
        outcome = fetch_page(
            url=url,
            domain=dom,
            user_agent=user_agent,
            timeout=timeout,
            rate_limit_delay=rate_delay,
            denylisted=False,
        )
        outcomes.append(outcome)
        repository.insert_serp_result(run_id, result, outcome.status, outcome.error)

        if outcome.status != "ok":
            if outcome.status.startswith("skipped"):
                pages_skipped += 1
            elif outcome.status.startswith("failed"):
                pages_failed += 1
            continue

        html = outcome.html or ""

        # Playwright fallback — only when static fetch returns a JS shell
        if is_js_shell(html) and pw_enabled:
            logger.info("[%s] JS shell detected → Playwright: %s", run_id, url)
            rendered = render_page(url, timeout_ms=pw_timeout)
            if rendered:
                html = rendered
                outcome.used_playwright = True
            else:
                outcome.status = "failed:render_error"
                outcome.error  = "Playwright render failed or unavailable"
                pages_failed  += 1
                continue

        pages_fetched += 1
        page_hits = extract_contacts(html, url)

        # Same-domain contact-page hop when homepage yield is thin
        if len(page_hits) < 2:
            contact_url = find_contact_page(html, url)
            if contact_url and contact_url not in seen_urls:
                seen_urls.add(contact_url)
                logger.debug("[%s] Contact-page hop → %s", run_id, contact_url)
                time.sleep(rate_delay)
                c_outcome = fetch_page(
                    url=contact_url,
                    domain=dom,
                    user_agent=user_agent,
                    timeout=timeout,
                    rate_limit_delay=0,
                    denylisted=False,
                )
                if c_outcome.status == "ok" and c_outcome.html:
                    extra = extract_contacts(c_outcome.html, contact_url)
                    page_hits.extend(extra)

        all_hits.extend(page_hits)

    # ── 7. Normalize + junk filter ────────────────────────────────────────────
    clean: List[ExtractionHit] = []
    for hit in all_hits:
        if hit.contact_type == "email":
            norm = normalize_email(hit.raw_value)
            if norm is None or is_junk_email(norm):
                continue
            hit.normalized_value = norm

        elif hit.contact_type == "phone":
            if is_junk_phone(hit.raw_value):
                continue
            norm = normalize_phone(hit.raw_value, default_region=phone_region)
            if norm is None:
                continue
            hit.normalized_value = norm

        else:
            continue  # unknown contact type

        clean.append(hit)

    # ── 8. Dedup across pages ─────────────────────────────────────────────────
    deduped = dedup_hits(clean)

    # ── 9. Store ──────────────────────────────────────────────────────────────
    stored = 0
    for hit in deduped:
        try:
            repository.upsert_lead(hit, run_id, query, source_engine)
            stored += 1
        except Exception as exc:
            logger.warning(
                "[%s] Storage error for %r: %s", run_id, hit.normalized_value, exc
            )

    repository.complete_run(run_id, len(serp_results), stored)
    logger.info("[%s] Done — %d contact(s) stored", run_id, stored)

    # ── 10. Response ──────────────────────────────────────────────────────────
    leads = repository.query_leads(query=query, limit=500)

    outcome_counts: Dict[str, int] = {}
    for o in outcomes:
        outcome_counts[o.status] = outcome_counts.get(o.status, 0) + 1

    return {
        "run_id":        run_id,
        "query":         query,
        "source_engine": source_engine,
        "leads":         leads,
        "summary": {
            "status":             "completed",
            "total_serp_results": len(serp_results),
            "pages_fetched":      pages_fetched,
            "pages_skipped":      pages_skipped,
            "pages_failed":       pages_failed,
            "skipped_denylisted": skipped_deny,
            "contacts_found":     stored,
            "outcome_breakdown":  outcome_counts,
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _is_denylisted(domain: str, denylist) -> bool:
    d = domain.lower()
    for deny in denylist:
        if d == deny or d.endswith("." + deny):
            return True
    return False


def _error_response(run_id: str, message: str) -> Dict[str, Any]:
    return {
        "run_id":  run_id,
        "error":   message,
        "leads":   [],
        "summary": {"status": "failed", "reason": message},
    }
