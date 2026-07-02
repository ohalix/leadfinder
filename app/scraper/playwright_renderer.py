"""
Playwright JS-rendering fallback.

ONLY invoked when static fetching produces a near-empty body that looks
like a client-side-rendered shell.  It is never the default fetch path.

If the `playwright` package is not installed the module degrades gracefully:
    - is_js_shell() still works (regex only)
    - render_page() logs a warning and returns None

Install (after pip install playwright):
    playwright install chromium
"""
from __future__ import annotations
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Optional import ──
_available = False
try:
    from playwright.sync_api import (  # type: ignore
        sync_playwright,
        TimeoutError as _PWTimeout,
    )
    _available = True
except ImportError:
    logger.info(
        "playwright not installed — JS fallback disabled.  "
        "pip install playwright && playwright install chromium"
    )

# ── Public helpers ──
_STRIP_TAGS = re.compile(r"<[^>]+>")

def is_js_shell(html: str) -> bool:
    """
    Returns True when the page looks like it requires JS execution to render.

    Heuristic: if the stripped body text is under 200 characters, or the
    total HTML is under 800 characters, it's almost certainly a JS shell.
    """
    if not html:
        return True
    if len(html.strip()) < 800:
        return True
    body_text = " ".join(_STRIP_TAGS.sub(" ", html).split())
    return len(body_text) < 200

def render_page(url: str, timeout_ms: int = 15_000) -> Optional[str]:
    """
    Renders *url* in a headless Chromium browser and returns the full HTML.
    Returns None on any failure so the caller can skip gracefully.

    This function is intentionally synchronous to keep the Flask app simple.
    """
    if not _available:
        logger.debug(f"Playwright unavailable — skipping render for {url}")
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="LeadFinderBot/0.1 (+https://yourdomain.example/bot)",
                java_script_enabled=True,
                ignore_https_errors=False,
            )
            page = ctx.new_page()
            page.set_default_navigation_timeout(timeout_ms)

            try:
                page.goto(url, wait_until="domcontentloaded")
                # Give the page a moment to settle after DOMContentLoaded
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except _PWTimeout:
                    # networkidle can hang on sites with long-polling; proceed anyway
                    logger.debug(f"networkidle timeout on {url} — using partial content")
            except _PWTimeout:
                logger.warning(f"Playwright navigation timeout on {url}")
                # Still try to grab whatever rendered
            except Exception as exc:
                logger.warning(f"Playwright navigation error on {url}: {exc}")
                browser.close()
                return None

            html = page.content()
            browser.close()
            logger.info(f"Playwright rendered {url} ({len(html)} chars)")
            return html
    except Exception as exc:
        logger.warning(f"Playwright render failure for {url}: {exc}")
        return None
