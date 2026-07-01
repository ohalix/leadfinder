"""
SerpAPI client.

Parses three first-class result sources:
  - organic_results
  - answer_box
  - ai_overview.references

Designed behind a thin factory so swapping providers is a one-file change.
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlparse

import httpx

from app.models import SerpResult

logger = logging.getLogger(__name__)

SERPAPI_BASE = "https://serpapi.com/search.json"


class SerpAPIError(Exception):
    pass


class SerpAPIClient:
    """
    Wraps the SerpAPI Google Search endpoint.
    One credit = one request, regardless of `num`.
    Paginates automatically when max_results > 10.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise SerpAPIError(
                "SERP_API_KEY is not set.  Add it to your .env file."
            )
        self._key = api_key

    # ── Public API ────────────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10) -> List[SerpResult]:
        """
        Run a search and return a flat, normalised list of SerpResult objects.
        Paginates using SerpAPI's `start` parameter until max_results is reached
        or the provider signals no more pages.
        """
        collected: List[SerpResult] = []
        page_size = min(max_results, 10)
        start = 0

        while len(collected) < max_results:
            want = min(page_size, max_results - len(collected))
            logger.debug(
                "SerpAPI request — query=%r start=%d num=%d", query, start, want
            )
            try:
                data = self._request(query, num=want, start=start)
            except SerpAPIError as exc:
                logger.error("SerpAPI error: %s", exc)
                break

            page = self._parse(data)
            collected.extend(page)

            # Stop if provider has no next page
            next_link = (
                data.get("serpapi_pagination", {}).get("next")
                or data.get("pagination", {}).get("next_link")
            )
            if not next_link or len(page) < want:
                break

            start += want

        return collected[:max_results]

    # ── Private helpers ───────────────────────────────────────────────────────

    def _request(self, query: str, num: int, start: int) -> dict:
        params = {
            "q": query,
            "api_key": self._key,
            "engine": "google",
            "num": num,
            "start": start,
            "hl": "en",
            "gl": "us",
        }
        try:
            resp = httpx.get(SERPAPI_BASE, params=params, timeout=30)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise SerpAPIError(
                f"HTTP {exc.response.status_code} from SerpAPI: {body}"
            ) from exc
        except httpx.RequestError as exc:
            raise SerpAPIError(f"SerpAPI request failed: {exc}") from exc

        data = resp.json()

        # SerpAPI signals errors inside the JSON body too
        if "error" in data:
            raise SerpAPIError(f"SerpAPI returned error: {data['error']}")

        return data

    def _parse(self, data: dict) -> List[SerpResult]:
        results: List[SerpResult] = []
        rank = 1

        # ── Organic results ───────────────────────────────────────────────────
        for item in data.get("organic_results", []):
            url = (item.get("link") or "").strip()
            if not url:
                continue
            results.append(
                SerpResult(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    domain=_domain(url),
                    rank=rank,
                    source_type="organic",
                )
            )
            rank += 1

        # ── Answer box ────────────────────────────────────────────────────────
        ab = data.get("answer_box") or {}
        ab_url = (
            ab.get("link")
            or (ab.get("source") or {}).get("link")
            or ""
        ).strip()
        if ab_url:
            snippet = (
                ab.get("answer")
                or ab.get("snippet")
                or ab.get("result")
                or ""
            )
            results.append(
                SerpResult(
                    url=ab_url,
                    title=ab.get("title", "Answer Box"),
                    snippet=snippet,
                    domain=_domain(ab_url),
                    rank=0,
                    source_type="answer_box",
                )
            )

        # ── AI Overview references ────────────────────────────────────────────
        for ref in (data.get("ai_overview") or {}).get("references") or []:
            url = (ref.get("url") or "").strip()
            if not url:
                continue
            results.append(
                SerpResult(
                    url=url,
                    title=ref.get("title", ""),
                    snippet=ref.get("snippet", ""),
                    domain=_domain(url),
                    rank=0,
                    source_type="ai_overview",
                )
            )

        return results


# ── Provider factory ──────────────────────────────────────────────────────────

def get_serp_client(provider: str, api_key: str) -> SerpAPIClient:
    """
    Returns the appropriate SERP client for `provider`.
    Currently only 'serpapi' is implemented; extend here for new providers.
    """
    norm = provider.lower().replace("-", "_").replace(" ", "_")
    if norm in ("serpapi", "serp_api"):
        return SerpAPIClient(api_key)
    raise ValueError(
        f"Unsupported SERP provider: {provider!r}.  "
        "Supported values: 'serpapi'"
    )


# ── Utilities ─────────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    """Extract bare hostname, strip www."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path.split("/")[0]
        return host.lower().replace("www.", "")
    except Exception:
        return ""
