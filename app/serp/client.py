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
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise SerpAPIError(
                f"SERP_API_KEY is not set.\nAdd it to your .env file."
            )
        self._key = api_key

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
            logger.info(
                f"SerpAPI request — query={query} start={start}"
            )
            try:
                data = self._request(query, start=start)
            except SerpAPIError as exc:
                logger.error(f"SerpAPI error: {exc}")
                raise SerpAPIError(f"{exc}") from exc

            page = self._parse(data)
            if not page:
                logger.info("SerpAPI: empty page received, stopping pagination")
                break

            collected.extend(page)
            logger.info(f"SerpAPI: page_len={len(page)} collected={len(collected)}")

            next_link = (
                data.get("serpapi_pagination", {}).get("next")
                or data.get("pagination", {}).get("next_link")
            )

            if not next_link:
                break

            start += page_size

        return collected[:max_results]

    def search_local_pack(self, query: str, location: str = "", max_results: int = 10) -> List[dict]:
        """
        Calls the SerpAPI Google Local Pack endpoint (engine=google_local) and
        returns a list of structured business dicts ready for direct injection
        into the lead pipeline.
        """
        params = {
            "q": query,
            "api_key": self._key,
            "engine": "google_local",
            "hl": "en",
            "gl": "us",
        }
        if location:
            params["location"] = location

        logger.info(f"SerpAPI local pack request — query={query!r}")
        try:
            resp = httpx.get(SERPAPI_BASE, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                f"SerpAPI local pack HTTP {exc.response.status_code}: "
                f"{exc.response.text[:200]}"
            )
            return []
        except httpx.RequestError as exc:
            logger.warning(f"SerpAPI local pack request error: {exc}")
            return []
        except Exception as exc:
            logger.warning(f"SerpAPI local pack unexpected error: {exc}")
            return []

        if "error" in data:
            logger.warning(f"SerpAPI local pack returned error: {data['error']}")
            return []

        local = data.get("local_results", {})
        if isinstance(local, list):
            places = local
        else:
            places = local.get("places", [])
        results = self._parse_local_places(places)
        results = results[:max_results]
        result_count = len(results)
        logger.info(f"SerpAPI local pack: {len(results)} place(s) returned")
        return results, result_count

    def _request(self, query: str, start: int) -> dict:
        params = {
            "q": query,
            "api_key": self._key,
            "engine": "google",
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
        if "error" in data:
            raise SerpAPIError(f"SerpAPI returned error: {data['error']}")

        return data

    def _parse(self, data: dict) -> List[SerpResult]:
        results: List[SerpResult] = []

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
                    rank=item.get("position", ""),
                    source_type="organic",
                )
            )

        if "ai_overview" in data:
            for block in (data.get("ai_overview") or {}).get("text_blocks") or []:
                if block.get("type") == "paragraph" and "snippet_links" in block:
                    for ref in block.get("snippet_links"):
                        url = (ref.get("link") or "").strip()
                        if not url:
                            continue
                        results.append(
                            SerpResult(
                                url= url,
                                title= ref.get("title", ""),
                                snippet= ref.get("snippet", ""),
                                domain= _domain(url),
                                rank= ref.get("index", ""),
                                source_type= "ai_overview"
                            )
                        )
                elif block.get("type") == "list":
                    for list_item in block.get("list"):
                        if "snippet_links" in list_item:
                            for ref in list_item.get("snippet_links"):
                                url = (ref.get("link") or "").strip()
                                if not url:
                                    continue
                                results.append(
                                    SerpResult(
                                        url= url,
                                        title= list_item.get("title", ""),
                                        snippet= ref.get("text", ""),
                                        domain= _domain(url),
                                        rank= ref.get("index", ""),
                                        source_type= "ai_overview"
                                    )
                                )
                
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
                        rank=ref.get("index", ""),
                        source_type="ai_overview",
                    )
                )
                
        if "places_sites" in data:
            for place in data.get("places_sites"):
                url = (place.get("link") or "").strip()
                if not url:
                    continue
                results.append(
                    SerpResult(
                        url=url,
                        title=place.get("title", ""),
                        snippet=place.get("snippet", ""),
                        domain=_domain(url),
                        rank=place.get("position", ""),
                        source_type="places_sites",
                    )
                )
            
        if "related_brands" in data:
            for related in data.get("related_brands"):
                rank = 1
                url = (related.get("link") or "").strip()
                if not url:
                    continue    
                results.append(
                    SerpResult(
                        url=url,
                        title=related.get("title", ""),
                        snippet=related.get("snippet", ""),
                        domain=_domain(url),
                        rank=rank,
                        source_type="related_brands",
                    )
                )
                rank =+ 1
        
        if "product_sites" in data:
            for product_site in data.get("product_sites"):
                url = (product_site.get("link") or "").strip()
                if not url:
                    continue    
                results.append(
                    SerpResult(
                        url=url,
                        title=product_site.get("title", ""),
                        snippet=product_site.get("snippet", ""),
                        domain=_domain(url),
                        rank=product_site.get("position" or ""),
                        source_type="product_sites",
                    )
                )
                
        if "local_results" in data:
            for place in (data.get("local_results") or {}).get("places") or []:
                url = (place.get("links") or {}).get("website", "").strip()
                if not url:
                    continue
                description = (place.get("description") or "").strip()
                results.append(
                    SerpResult(
                        url=url,
                        title=place.get("title", ""),
                        snippet=description,
                        domain=_domain(url),
                        rank=place.get("position", ""),
                        source_type="local_pack",
                    )
                )
        
        return results

    def _parse_local_places(self, places: list) -> List[dict]:
        results = []
        for place in places:
            phone   = (place.get("phone") or "").strip()
            website = (place.get("links") or {}).get("website", "").strip()

            if not phone and not website:
                continue

            results.append({
                "title":             place.get("title", ""),
                "phone":             phone,
                "website":           website,
                "domain":            _domain(website) if website else "",
                "address":           place.get("address", ""),
                "type":              place.get("type", ""),
                "rating":            place.get("rating"),
                "reviews":           place.get("reviews"),
                "description":       (place.get("description") or "").strip(),
                "hours":             place.get("hours", ""),
                "years_in_business": place.get("years_in_business", ""),
                "position":          place.get("position"),
                "gps_coordinates":   place.get("gps_coordinates"),
                "place_id":          place.get("place_id", ""),
            })
        return results

def get_serp_client(provider: str, api_key: str) -> SerpAPIClient:
    norm = provider.lower().replace("-", "_").replace(" ", "_")
    if norm in ("serpapi", "serp_api"):
        return SerpAPIClient(api_key)
    raise ValueError(
        f"Unsupported SERP provider: {provider!r}.  "
        "Supported values: 'serpapi'"
    )

def _domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path.split("/")[0]
        return host.lower().replace("www.", "")
    except Exception:
        return ""
