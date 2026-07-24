"""
HTTP page fetcher.

- Uses httpx for consistent sync/async API and per-phase timeouts.
- Retries only on transient failures (timeout, 5xx, connection reset).
- Never retries 403 / 404 / 410 — those mean skip-and-log.
- Returns a typed FetchOutcome so callers never raise, they always branch.
"""
from __future__ import annotations
import logging
import time
from typing import Optional
import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from app.models import FetchOutcome
from app.scraper.robots import is_allowed

logger = logging.getLogger(__name__)
_TRANSIENT_CODES = {429, 500, 502, 503, 504}


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_CODES
    return False

def _is_html(content_type: str) -> bool:
    ct = content_type.lower()
    return "text/html" in ct or "application/xhtml" in ct

@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=10),
    reraise=True,
)
def _get(url: str, user_agent: str, timeout: int) -> httpx.Response:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive"
    }
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        return client.get(url, headers=headers)

def fetch_page(
    url: str,
    domain: str,
    user_agent: str,
    timeout: int,
    rate_limit_delay: float,
    denylisted: bool = False,
) -> FetchOutcome:
    """
    Fetch one URL.  Returns a FetchOutcome — never raises.

    Outcome status values:
        ok                – HTML retrieved, html field is populated
        skipped:denylisted
        skipped:robots
        skipped:non_html  – successful response but not HTML content
        failed:timeout
        failed:http_error
    """
    if denylisted:
        return FetchOutcome(url=url, status="skipped:denylisted")

    if not is_allowed(url, domain, user_agent, timeout=5):
        logger.info(f"robots.txt disallows {url}")
        return FetchOutcome(url=url, status="skipped:robots")

    if rate_limit_delay > 0:
        time.sleep(rate_limit_delay)

    try:
        resp = _get(url, user_agent, timeout)
    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching {url}")
        return FetchOutcome(url=url, status="failed:timeout", error="Request timed out")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        logger.info(f"HTTP {code} for {url}")
        return FetchOutcome(url=url, status="failed:http_error", error=f"HTTP {code}")
    except httpx.RequestError as exc:
        logger.warning(f"Request error for {url}: {exc}")
        return FetchOutcome(url=url, status="failed:http_error", error=str(exc)[:200])
    except Exception as exc:
        logger.warning(f"Unexpected error for {url}: {exc}")
        return FetchOutcome(url=url, status="failed:http_error", error=str(exc)[:200])

    ct = resp.headers.get("content-type", "")
    if not _is_html(ct):
        return FetchOutcome(
            url=url,
            status="skipped:non_html",
            error=f"Content-Type: {ct[:80]}",
        )

    return FetchOutcome(url=url, status="ok", html=resp.text)
