"""
JSON API routes.

All routes are intentionally thin:
  parse request → call service or repository → serialize response.

No business logic, no SQL, no HTTP fetching lives here.
"""
from __future__ import annotations

import csv
import io
import logging

from flask import Blueprint, Response, current_app, jsonify, request

from app.services.search_service import run_search
from app.storage import repository

api_bp = Blueprint("api", __name__)
logger = logging.getLogger(__name__)


# ── Health ────────────────────────────────────────────────────────────────────

@api_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "leadfinder"})


# ── Search ────────────────────────────────────────────────────────────────────

@api_bp.route("/search", methods=["POST"])
def search():
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    max_results = int(body.get("max_results") or current_app.config.get("SERP_MAX_RESULTS", 10))

    if not query:
        return jsonify({"error": "query is required"}), 400
    if not (1 <= max_results <= 100):
        return jsonify({"error": "max_results must be between 1 and 100"}), 400

    result = run_search(query, current_app.config, max_results=max_results)
    status_code = 502 if "error" in result and not result.get("leads") else 200
    return jsonify(result), status_code


# ── Leads ─────────────────────────────────────────────────────────────────────

@api_bp.route("/leads", methods=["GET"])
def leads():
    query        = request.args.get("query") or None
    domain       = request.args.get("domain") or None
    confidence   = request.args.get("confidence") or None
    contact_type = request.args.get("contact_type") or None
    limit        = min(int(request.args.get("limit") or 200), 500)
    offset       = int(request.args.get("offset") or 0)

    data = repository.query_leads(
        query=query, domain=domain,
        confidence=confidence, contact_type=contact_type,
        limit=limit, offset=offset,
    )
    return jsonify({"leads": data, "count": len(data)})


@api_bp.route("/leads/export", methods=["GET"])
def leads_export():
    query        = request.args.get("query") or None
    domain       = request.args.get("domain") or None
    confidence   = request.args.get("confidence") or None
    contact_type = request.args.get("contact_type") or None

    data = repository.query_leads(
        query=query, domain=domain,
        confidence=confidence, contact_type=contact_type,
        limit=5000, offset=0,
    )

    output = io.StringIO()
    fieldnames = [
        "id", "contact_type", "normalized_value", "raw_value",
        "domain", "method", "confidence", "seen_count",
        "first_seen", "last_seen", "source_urls", "queries", "source_engine",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for row in data:
        row["source_urls"] = "; ".join(row.get("source_urls") or [])
        row["queries"]     = "; ".join(row.get("queries") or [])
        writer.writerow(row)

    csv_data = output.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads_export.csv"'},
    )


# ── Stats ─────────────────────────────────────────────────────────────────────

@api_bp.route("/stats", methods=["GET"])
def stats():
    return jsonify({
        "total_leads": repository.count_leads(),
        "total_runs":  repository.count_runs(),
        "by_confidence": repository.leads_by_confidence(),
        "by_type":       repository.leads_by_type(),
    })


# ── Runs ──────────────────────────────────────────────────────────────────────

@api_bp.route("/runs", methods=["GET"])
def runs():
    limit = min(int(request.args.get("limit") or 50), 100)
    data  = repository.get_all_runs(limit=limit)
    return jsonify({"runs": data, "count": len(data)})


@api_bp.route("/runs/<run_id>", methods=["GET"])
def run_detail(run_id: str):
    run = repository.get_run(run_id)
    if not run:
        return jsonify({"error": "run not found"}), 404
    return jsonify(run)
