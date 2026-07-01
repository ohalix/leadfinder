"""
Database initialisation and connection management.

Three-table schema:
  search_runs   – one row per /api/search call
  serp_results  – one row per URL returned by the SERP provider
  leads         – one row per unique (contact_type, normalized_value) pair
  lead_sources  – many rows per lead; records every run/URL that found it

Uses WAL journal mode for safe concurrent reads during writes.
No ORM – raw sqlite3 only.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)

# Module-level path set by init_db() at app startup
_db_path: str = ""


def init_db(app) -> None:
    """Called once from the app factory.  Creates tables if they don't exist."""
    global _db_path
    _db_path = app.config["DATABASE_PATH"]

    # Ensure the data directory exists
    data_dir = os.path.dirname(_db_path)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)

    with _connect() as conn:
        _create_schema(conn)

    logger.info("Database ready: %s", _db_path)


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """
    Yields a connection with auto-commit on clean exit and rollback on error.
    Always closes the connection on exit.
    """
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Private ───────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        _db_path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, faster than FULL
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    -- ── search_runs ───────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS search_runs (
        id                   TEXT    PRIMARY KEY,
        query                TEXT    NOT NULL,
        source_engine        TEXT    NOT NULL DEFAULT 'serpapi',
        created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
        status               TEXT    NOT NULL DEFAULT 'running',
        total_result_count   INTEGER NOT NULL DEFAULT 0,
        total_contacts_found INTEGER NOT NULL DEFAULT 0
    );

    -- ── serp_results ──────────────────────────────────────────────────────
    -- One row per URL returned by SERP (including skipped/failed ones).
    CREATE TABLE IF NOT EXISTS serp_results (
        id          TEXT    PRIMARY KEY,
        run_id      TEXT    NOT NULL,
        url         TEXT    NOT NULL,
        domain      TEXT    NOT NULL,
        title       TEXT,
        snippet     TEXT,
        rank        INTEGER,
        source_type TEXT    DEFAULT 'organic',
        fetch_status TEXT,
        fetch_error  TEXT,
        FOREIGN KEY (run_id) REFERENCES search_runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_serp_run ON serp_results(run_id);
    CREATE INDEX IF NOT EXISTS idx_serp_domain ON serp_results(domain);

    -- ── leads ─────────────────────────────────────────────────────────────
    -- Deduplication table: UNIQUE on (contact_type, normalized_value).
    -- seen_count / first_seen / last_seen accumulate across all runs.
    CREATE TABLE IF NOT EXISTS leads (
        id               TEXT    PRIMARY KEY,
        contact_type     TEXT    NOT NULL,
        raw_value        TEXT    NOT NULL,
        normalized_value TEXT    NOT NULL,
        domain           TEXT,
        method           TEXT,
        confidence       TEXT,
        seen_count       INTEGER NOT NULL DEFAULT 1,
        first_seen       TEXT    NOT NULL DEFAULT (datetime('now')),
        last_seen        TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(contact_type, normalized_value)
    );
    CREATE INDEX IF NOT EXISTS idx_leads_domain     ON leads(domain);
    CREATE INDEX IF NOT EXISTS idx_leads_confidence ON leads(confidence);
    CREATE INDEX IF NOT EXISTS idx_leads_type       ON leads(contact_type);

    -- ── lead_sources ──────────────────────────────────────────────────────
    -- Many-to-many between leads and runs; provenance trail.
    CREATE TABLE IF NOT EXISTS lead_sources (
        id            TEXT PRIMARY KEY,
        lead_id       TEXT NOT NULL,
        run_id        TEXT NOT NULL,
        query         TEXT NOT NULL,
        source_engine TEXT NOT NULL DEFAULT 'serpapi',
        source_url    TEXT NOT NULL,
        FOREIGN KEY (lead_id) REFERENCES leads(id),
        FOREIGN KEY (run_id)  REFERENCES search_runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_src_lead ON lead_sources(lead_id);
    CREATE INDEX IF NOT EXISTS idx_src_run  ON lead_sources(run_id);
    CREATE INDEX IF NOT EXISTS idx_src_query ON lead_sources(query);
    """)
    conn.commit()
