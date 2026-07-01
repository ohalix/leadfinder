"""
Configuration loaded from environment via python-dotenv.

All values are plain class attributes so Flask's from_object() copies them
correctly. (Properties are not copied by from_object when passed a class.)
"""
import os
from typing import FrozenSet

from dotenv import load_dotenv

load_dotenv()

_DENYLIST_RAW: str = os.getenv(
    "DOMAIN_DENYLIST",
    "linkedin.com,facebook.com,twitter.com,x.com,yelp.com,indeed.com,"
    "wikipedia.org,amazon.com,ebay.com,youtube.com,instagram.com,"
    "glassdoor.com,monster.com,ziprecruiter.com,reddit.com,quora.com",
)


class Config:
    # ── Flask ─────────────────────────────────────────────────────────────────
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-production")

    # ── SERP ──────────────────────────────────────────────────────────────────
    SERP_API_KEY: str = os.getenv("SERP_API_KEY", "")
    SERP_PROVIDER: str = os.getenv("SERP_PROVIDER", "serpapi")
    SERP_MAX_RESULTS: int = int(os.getenv("SERP_MAX_RESULTS", "10"))

    # ── Scraper ───────────────────────────────────────────────────────────────
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RATE_LIMIT_DELAY: float = float(os.getenv("RATE_LIMIT_DELAY_SECONDS", "1.0"))
    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "LeadFinderBot/0.1 (+https://yourdomain.example/bot)",
    )

    # ── Playwright ────────────────────────────────────────────────────────────
    PLAYWRIGHT_ENABLED: bool = os.getenv("PLAYWRIGHT_ENABLED", "true").lower() == "true"
    PLAYWRIGHT_TIMEOUT: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "15000"))

    # ── Storage ───────────────────────────────────────────────────────────────
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/leads.db")

    # ── Phone ─────────────────────────────────────────────────────────────────
    DEFAULT_PHONE_REGION: str = os.getenv("DEFAULT_PHONE_REGION", "US")

    # ── Domain denylist ───────────────────────────────────────────────────────
    # Computed as a plain attribute so from_object() copies the frozenset value,
    # not a property descriptor.
    DOMAIN_DENYLIST: FrozenSet[str] = frozenset(
        d.strip().lower() for d in _DENYLIST_RAW.split(",") if d.strip()
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
