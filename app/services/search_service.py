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


def run_search(query: str, config: Any, max_results: Optional[int] = None, local_pack_max_results: int = 10) -> Dict[str, Any]:
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
    logger.info(f"[{run_id}] Search started: {query!r}  max={max_results:,d}")

    # ── 1. SERP ──
    try:
        client = get_serp_client(source_engine, api_key)
        serp_results: List[SerpResult] = client.search(query, max_results=max_results)
    except (SerpAPIError, ValueError) as exc:
        logger.error(f"[{run_id}] SERP failed: {exc}")
        repository.complete_run(run_id, 0, 0, "failed")
        return _error_response(run_id, str(exc))

    logger.info(f"[{run_id}] SERP returned {len(serp_results):,d} result(s)")

    # ── 2. Denylist filter ──
    allowed: List[SerpResult] = []
    skipped_deny = 0
    for r in serp_results:
        if _is_denylisted(r.domain, denylist):
            skipped_deny += 1
            repository.insert_serp_result(run_id, r, "skipped:denylisted", None)
        else:
            allowed.append(r)

    logger.info(
        f"[{run_id}] After denylist: {len(allowed):,d} allowed, {skipped_deny:,d} skipped"
    )

    # ── 3–6. Fetch / extract loop ──
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
            logger.info(f"[{run_id}] JS shell detected → Playwright: {url}")
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
                logger.debug(f"[{run_id}] Contact-page hop → {contact_url}")
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
        
    # ── 3b. Local Pack direct contacts (Task 3) ──
    # Calls the Google Local Pack API endpoint separately and injects phone
    # numbers directly into all_hits, bypassing the scraping step.
    # Failures are logged and skipped; they never abort the rest of the pipeline.
    lp_phones_injected = 0
    lp_pages_scraped   = 0
    try:
        local_places, lp_result_count = client.search_local_pack(query, max_results=local_pack_max_results)
        for place in local_places:
            phone   = (place.get("phone") or "").strip()
            website = (place.get("website") or "").strip()
            
            if phone:
                all_hits.append(ExtractionHit(
                    contact_type="phone",
                    raw_value=phone,
                    normalized_value=phone,
                    method="local_pack",
                    confidence="high",
                    source_url=website or place.get("title", ""),
                ))
                lp_phones_injected += 1
            
            if not website or website in seen_urls:
                continue
            
            seen_urls.add(website)
            dom = _domain(website)
            if _is_denylisted(dom, denylist):
                continue

            time.sleep(rate_delay)
            lp_outcome = fetch_page(
                url=website,
                domain=dom,
                user_agent=user_agent,
                timeout=timeout,
                rate_limit_delay=0,
                denylisted=False,
            )

            if lp_outcome.status != "ok" or not lp_outcome.html:
                continue

            lp_html = lp_outcome.html
            if is_js_shell(lp_html) and pw_enabled:
                rendered = render_page(website, timeout_ms=pw_timeout)
                if rendered:
                    lp_html = rendered

            lp_hits = extract_contacts(lp_html, website)
            if len(lp_hits) < 2:
                contact_url = find_contact_page(lp_html, website)
                if contact_url and contact_url not in seen_urls:
                    seen_urls.add(contact_url)
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
                        lp_hits.extend(extract_contacts(c_outcome.html, contact_url))

            all_hits.extend(lp_hits)
            lp_pages_scraped += 1

        if lp_phones_injected or lp_pages_scraped:
            logger.info(
                f"[{run_id}] Local pack: {lp_phones_injected} phone(s) injected, "
                f"{lp_pages_scraped} website(s) scraped"
            )   
    except Exception as exc:
        logger.warning(f"[{run_id}] Local pack direct search failed (non-fatal): {exc}")

    # ── 7. Normalize + junk filter ──
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

    # ── 8. Dedup across pages ──
    deduped = dedup_hits(clean)

    # ── 9. Store ──
    stored = 0
    for hit in deduped:
        try:
            repository.upsert_lead(hit, run_id, query, source_engine)
            stored += 1
        except Exception as exc:
            logger.warning(
                f"[{run_id}] Storage error for {hit.normalized_value!r}: {exc}"
            )

    repository.complete_run(run_id, (len(serp_results) + lp_result_count), stored)
    logger.info(f"[{run_id}] Done — {stored:,d} contact(s) stored")

    # ── 10. Response ──
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
            "local_pack_phones":   lp_phones_injected,
            "local_pack_pages_scraped": lp_pages_scraped,
        },
    }

# ── Helpers ──
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
