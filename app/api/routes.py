from __future__ import annotations
import csv
import io
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from flask import Blueprint, Response, current_app, jsonify, request
from app.services.search_service import run_search
from app.storage import repository
from app.email.sender import send_leads_email

api_bp = Blueprint("api", __name__)
logger = logging.getLogger(__name__)

# ── Task 2: Bounded thread pool for concurrent search isolation ──
# max_workers=3 limits simultaneous searches; overflow requests
# queue automatically inside the pool until a worker is free.
# Each worker runs in an isolated thread with a snapshotted config dict,
# so Flask's application context is never accessed from background threads.
_search_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="search")


# ── Health ──
@api_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "leadfinder"})


# ── Queue status ──
@api_bp.route("/queue/status", methods=["GET"])
def queue_status():
    return jsonify({
        "max_workers": 3,
        "note": "Up to 3 searches run concurrently; additional requests queue automatically.",
    })


# ── Search ──
@api_bp.route("/search", methods=["POST"])
def search():
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    raw_max = body.get("max_results")
    try:
        max_results = int(raw_max) if raw_max is not None else current_app.config.get("SERP_MAX_RESULTS", 10)
    except (ValueError, TypeError):
        max_results = 10
    max_results = max(10, min(100, (max_results // 10) * 10)) if max_results >= 10 else 10

    raw_lp = body.get("local_pack_max_results")
    try:
        local_pack_max_results = int(raw_lp) if raw_lp is not None else 10
    except (ValueError, TypeError):
        local_pack_max_results = 10
    local_pack_max_results = max(1, min(60, local_pack_max_results))

    if not query:
        return jsonify({"error": "query is required"}), 400

    # Snapshot config into a plain dict before handing to the thread.
    # current_app / application context is not available inside pool threads.
    config_snapshot = dict(current_app.config)

    try:
        future = _search_pool.submit(
            run_search, query, config_snapshot, max_results, local_pack_max_results
        )
        result = future.result(timeout=300)  # 5-minute hard cap per search
    except FutureTimeoutError:
        logger.error(f"Search timed out for query={query!r}")
        return jsonify({"error": "Search timed out after 5 minutes"}), 504
    except Exception as exc:
        logger.error(f"Search pool error for query={query!r}: {exc}")
        return jsonify({"error": str(exc)}), 500

    status_code = 502 if "error" in result and not result.get("leads") else 200
    return jsonify(result), status_code

# ── Leads ──
@api_bp.route("/leads", methods=["GET"])
def leads():
    query        = request.args.get("query") or None
    domain       = request.args.get("domain") or None
    confidence   = request.args.get("confidence") or None
    contact_type = request.args.get("contact_type") or None
    date_from    = request.args.get("date_from") or None
    date_to      = request.args.get("date_to") or None
    limit        = min(int(request.args.get("limit") or 200), 500)
    offset       = int(request.args.get("offset") or 0)

    data = repository.query_leads(
        query=query, domain=domain,
        confidence=confidence, contact_type=contact_type,
        date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )
    return jsonify({"leads": data, "count": len(data)})

@api_bp.route("/leads/export", methods=["GET"])
def leads_export():
    query        = request.args.get("query") or None
    domain       = request.args.get("domain") or None
    confidence   = request.args.get("confidence") or None
    contact_type = request.args.get("contact_type") or None
    date_from    = request.args.get("date_from") or None
    date_to      = request.args.get("date_to") or None

    data = repository.query_leads(
        query=query, domain=domain,
        confidence=confidence, contact_type=contact_type,
        date_from=date_from, date_to=date_to,
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

# ── Stats ──
@api_bp.route("/stats", methods=["GET"])
def stats():
    return jsonify({
        "total_leads": repository.count_leads(),
        "total_runs":  repository.count_runs(),
        "by_confidence": repository.leads_by_confidence(),
        "by_type":       repository.leads_by_type(),
    })

# ── Runs ──
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


# ── Email ──
@api_bp.route("/email/preview", methods=["POST"])
def email_preview():
    """
    Returns the first 5 leads that would be targeted by the given filters,
    with the subject and body rendered for each one. No email is sent.
    """
    body      = request.get_json(force=True, silent=True) or {}
    query     = body.get("query") or None
    domain    = body.get("domain") or None
    confidence = body.get("confidence") or None
    date_from = body.get("date_from") or None
    date_to   = body.get("date_to") or None
    subject_tpl = (body.get("subject") or "").strip()
    body_tpl    = (body.get("body") or "").strip()

    if not subject_tpl or not body_tpl:
        return jsonify({"error": "subject and body are required"}), 400

    leads = repository.query_leads(
        query=query, domain=domain,
        confidence=confidence, contact_type="email",
        date_from=date_from, date_to=date_to,
        limit=5, offset=0,
    )

    previews = []
    for lead in leads:
        q = (lead.get("queries") or [""])[0]
        try:
            previews.append({
                "to":      lead["normalized_value"],
                "subject": subject_tpl.format(
                    email=lead["normalized_value"],
                    domain=lead.get("domain", ""),
                    query=q,
                ),
                "body": body_tpl.format(
                    email=lead["normalized_value"],
                    domain=lead.get("domain", ""),
                    query=q,
                ),
            })
        except KeyError as exc:
            previews.append({
                "to": lead["normalized_value"],
                "error": f"Template variable not found: {exc}",
            })

    return jsonify({"previews": previews, "total_would_send": _count_email_leads(
        query, domain, confidence, date_from, date_to
    )})


@api_bp.route("/email/send", methods=["POST"])
def email_send():
    """
    Sends emails to all leads matching the given filters.
    Uses the SMTP config from the application config.
    Runs in the search thread pool (non-blocking for other requests).
    """
    body       = request.get_json(force=True, silent=True) or {}
    query      = body.get("query") or None
    domain     = body.get("domain") or None
    confidence = body.get("confidence") or None
    date_from  = body.get("date_from") or None
    date_to    = body.get("date_to") or None
    subject_tpl  = (body.get("subject") or "").strip()
    body_tpl     = (body.get("body") or "").strip()
    rate_delay   = float(body.get("rate_delay") or current_app.config.get("RATE_LIMIT_DELAY", 1.0))
    limit        = min(int(body.get("limit") or 500), 1000)

    if not subject_tpl or not body_tpl:
        return jsonify({"error": "subject and body are required"}), 400

    smtp_cfg = {
        "host":      current_app.config.get("SMTP_HOST", ""),
        "port":      current_app.config.get("SMTP_PORT", 587),
        "username":  current_app.config.get("SMTP_USERNAME", ""),
        "password":  current_app.config.get("SMTP_PASSWORD", ""),
        "from_email": current_app.config.get("SMTP_FROM", ""),
        "from_name":  current_app.config.get("SMTP_FROM_NAME", "LeadFinder"),
        "use_tls":   current_app.config.get("SMTP_USE_TLS", True),
    }

    if not smtp_cfg["host"] or not smtp_cfg["username"]:
        return jsonify({"error": "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME, and SMTP_PASSWORD in your .env file."}), 503

    leads = repository.query_leads(
        query=query, domain=domain,
        confidence=confidence, contact_type="email",
        date_from=date_from, date_to=date_to,
        limit=limit, offset=0,
    )

    if not leads:
        return jsonify({"sent": 0, "failed": 0, "results": [], "message": "No leads matched the filters"}), 200

    config_snapshot = dict(current_app.config)

    try:
        future = _search_pool.submit(
            send_leads_email,
            smtp_cfg, leads, subject_tpl, body_tpl, rate_delay
        )
        results = future.result(timeout=600)
    except FutureTimeoutError:
        return jsonify({"error": "Email send timed out"}), 504
    except Exception as exc:
        logger.error(f"Email send failed: {exc}")
        return jsonify({"error": str(exc)}), 500

    sent   = sum(1 for r in results if r["status"] == "sent")
    failed = sum(1 for r in results if r["status"] == "failed")

    return jsonify({
        "sent":    sent,
        "failed":  failed,
        "results": results,
    })


def _count_email_leads(query, domain, confidence, date_from, date_to):
    leads = repository.query_leads(
        query=query, domain=domain,
        confidence=confidence, contact_type="email",
        date_from=date_from, date_to=date_to,
        limit=5000, offset=0,
    )
    return len(leads)
