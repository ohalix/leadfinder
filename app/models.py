from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

@dataclass
class SerpResult:
    url: str
    title: str
    snippet: str
    domain: str
    rank: int
    # organic | answer_box | ai_overview
    source_type: str = "organic"

@dataclass
class FetchOutcome:
    url: str
    # ok | skipped:robots | skipped:non_html | skipped:denylisted |
    # failed:timeout | failed:http_error | failed:render_error
    status: str
    error: Optional[str] = None
    html: Optional[str] = None
    used_playwright: bool = False


@dataclass
class ExtractionHit:
    contact_type: str          # email | phone
    raw_value: str
    normalized_value: str      # filled in by normalize layer
    # schema | mailto | tel | footer | text | playwright
    method: str
    # high | medium | low  (re-scored by confidence.py)
    confidence: str
    source_url: str

@dataclass
class Lead:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    contact_type: str = ""
    normalized_value: str = ""
    raw_value: str = ""
    domain: str = ""
    confidence: str = ""
    method: str = ""
    source_urls: List[str] = field(default_factory=list)
    query: str = ""
    seen_count: int = 1
    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)

@dataclass
class RunSummary:
    run_id: str
    query: str
    source_engine: str
    total_result_count: int = 0
    total_contacts_found: int = 0
    pages_fetched: int = 0
    pages_skipped: int = 0
    pages_failed: int = 0
    outcomes: List[FetchOutcome] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "completed"
