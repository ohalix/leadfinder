from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

import httpx
from protego import Protego

logger = logging.getLogger(__name__)

# domain → (Protego instance or None, crawl_delay_seconds)
_cache: Dict[str, Tuple[Optional[Protego], float]] = {}


def is_allowed(url: str, domain: str, user_agent: str, timeout: int = 5) -> bool:
    """
    Returns True if fetching *url* is permitted by the domain's robots.txt.

    - Fails open: if robots.txt cannot be fetched, we assume allowed.
    - Enforces crawl-delay in-process when the rule specifies one.
    """
    if domain not in _cache:
        _cache[domain] = _load(domain, user_agent, timeout)

    rp, delay = _cache[domain]
    if rp is None:
        return True  # no robots.txt or parse failure → assume allowed

    allowed: bool = rp.can_fetch(url, user_agent)
    if allowed and delay > 0:
        logger.debug(f"robots crawl-delay {delay:.1f}s for {domain}")
        time.sleep(delay)

    return allowed


def clear_cache() -> None:
    """Reset the per-process cache (useful in tests)."""
    _cache.clear()


# ── Private ──
def _load(
    domain: str, user_agent: str, timeout: int
) -> Tuple[Optional[Protego], float]:
    raw = _fetch_robots_txt(domain, user_agent, timeout)
    if raw is None:
        return None, 0.0

    try:
        rp = Protego.parse(raw)
        delay = float(rp.crawl_delay(user_agent) or 0.0)
        logger.debug(f"robots.txt loaded for {domain} (crawl-delay={delay:.1f}s)")
        return rp, delay
    except Exception as exc:
        logger.debug(f"Failed to parse robots.txt for {domain}: {exc}")
        return None, 0.0


def _fetch_robots_txt(domain: str, user_agent: str, timeout: int) -> Optional[str]:
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}/robots.txt"
        try:
            resp = httpx.get(
                url,
                timeout=timeout,
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
    logger.debug(f"Could not fetch robots.txt for {domain} — assuming allowed")
    return None
