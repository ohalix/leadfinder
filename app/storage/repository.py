from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from app.models import ExtractionHit, SerpResult
from app.storage.db import get_conn

logger = logging.getLogger(__name__)

# ── Utilities ──
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _uid() -> str:
    return str(uuid.uuid4())

# ── Search run ──
def create_run(run_id: str, query: str, source_engine: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO search_runs (id, query, source_engine, created_at, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (run_id, query, source_engine, _now()),
        )

def complete_run(run_id: str, total_results: int, total_contacts: int, status: str = "completed") -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE search_runs
               SET status=?, total_result_count=?, total_contacts_found=?
               WHERE id=?""",
            (status, total_results, total_contacts, run_id),
        )

def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM search_runs WHERE id=?", (run_id,)
        ).fetchone()
    return dict(row) if row else None

def get_all_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM search_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]

# ── SERP results ──
def insert_serp_result(run_id: str, result: SerpResult, fetch_status: str, fetch_error: Optional[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO serp_results
               (id, run_id, url, domain, title, snippet, rank, source_type,
                fetch_status, fetch_error)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                _uid(), run_id, result.url, result.domain,
                result.title, result.snippet, result.rank, result.source_type,
                fetch_status, fetch_error,
            ),
        )

# ── Leads ──
def upsert_lead(hit: ExtractionHit, run_id: str, query: str, source_engine: str) -> str:
    """
    Insert a new lead or update seen_count + last_seen on duplicate.
    Always appends a lead_sources row (provenance trail).
    Returns the lead's UUID.
    """
    now = _now()

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, seen_count FROM leads WHERE contact_type=? AND normalized_value=?",
            (hit.contact_type, hit.normalized_value),
        ).fetchone()

        if row:
            lead_id   = row["id"]
            new_count = row["seen_count"] + 1
            conn.execute(
                "UPDATE leads SET seen_count=?, last_seen=? WHERE id=?",
                (new_count, now, lead_id),
            )
        else:
            lead_id = _uid()
            # Derive domain from source URL
            try:
                from urllib.parse import urlparse
                dom = urlparse(hit.source_url).netloc.lower().replace("www.", "")
            except Exception:
                dom = ""

            conn.execute(
                """INSERT INTO leads
                   (id, contact_type, raw_value, normalized_value,
                    domain, method, confidence, seen_count, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,1,?,?)""",
                (
                    lead_id, hit.contact_type, hit.raw_value,
                    hit.normalized_value, dom, hit.method,
                    hit.confidence, now, now,
                ),
            )

        # Always record provenance
        conn.execute(
            """INSERT INTO lead_sources
               (id, lead_id, run_id, query, source_engine, source_url)
               VALUES (?,?,?,?,?,?)""",
            (_uid(), lead_id, run_id, query, source_engine, hit.source_url),
        )

    return lead_id


def query_leads(query: Optional[str] = None, domain: Optional[str] = None, confidence: Optional[str] = None, contact_type: Optional[str] = None, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Filtered lead list with aggregated source URLs and queries.
    Ordered by seen_count DESC, last_seen DESC.
    """
    conditions: List[str] = []
    params: List[Any] = []

    if query:
        conditions.append("ls.query LIKE ?")
        params.append(f"%{query}%")
    if domain:
        conditions.append("l.domain LIKE ?")
        params.append(f"%{domain}%")
    if confidence:
        conditions.append("l.confidence = ?")
        params.append(confidence)
    if contact_type:
        conditions.append("l.contact_type = ?")
        params.append(contact_type)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            l.id,
            l.contact_type,
            l.raw_value,
            l.normalized_value,
            l.domain,
            l.method,
            l.confidence,
            l.seen_count,
            l.first_seen,
            l.last_seen,
            GROUP_CONCAT(DISTINCT ls.source_url) AS source_urls,
            GROUP_CONCAT(DISTINCT ls.query)      AS queries,
            ls.source_engine
        FROM leads l
        LEFT JOIN lead_sources ls ON ls.lead_id = l.id
        {where}
        GROUP BY l.id
        ORDER BY l.seen_count DESC, l.last_seen DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["source_urls"] = d["source_urls"].split(",") if d["source_urls"] else []
        d["queries"]     = list(set(d["queries"].split(","))) if d["queries"] else []
        results.append(d)

    return results


# ── Counts ──

def count_leads() -> int:
    with get_conn() as conn:
        r = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()
    return r["c"] if r else 0

def count_runs() -> int:
    with get_conn() as conn:
        r = conn.execute("SELECT COUNT(*) AS c FROM search_runs").fetchone()
    return r["c"] if r else 0

def leads_by_confidence() -> Dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT confidence, COUNT(*) AS c FROM leads GROUP BY confidence"
        ).fetchall()
    return {r["confidence"]: r["c"] for r in rows}

def leads_by_type() -> Dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT contact_type, COUNT(*) AS c FROM leads GROUP BY contact_type"
        ).fetchall()
    return {r["contact_type"]: r["c"] for r in rows}