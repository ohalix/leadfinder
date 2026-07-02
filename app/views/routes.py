"""
Frontend (template) routes.

These serve HTML pages — all data fetching for the UI happens via the
/api/* JSON routes called from JavaScript in the templates.
"""
from __future__ import annotations
import logging
from flask import Blueprint, render_template, request
from app.storage import repository

views_bp = Blueprint("views", __name__)
logger = logging.getLogger(__name__)


@views_bp.route("/")
def index():
    total_leads      = repository.count_leads()
    total_runs       = repository.count_runs()
    recent_runs      = repository.get_all_runs(limit=5)
    by_confidence    = repository.leads_by_confidence()
    by_type          = repository.leads_by_type()
    return render_template(
        "index.html",
        total_leads=total_leads,
        total_runs=total_runs,
        recent_runs=recent_runs,
        by_confidence=by_confidence,
        by_type=by_type,
    )

@views_bp.route("/search")
def search():
    return render_template("search.html")

@views_bp.route("/leads")
def leads():
    q            = request.args.get("query") or ""
    domain       = request.args.get("domain") or ""
    confidence   = request.args.get("confidence") or ""
    contact_type = request.args.get("contact_type") or ""

    leads_data = repository.query_leads(
        query=q or None,
        domain=domain or None,
        confidence=confidence or None,
        contact_type=contact_type or None,
        limit=200,
        offset=0,
    )

    return render_template(
        "leads.html",
        leads=leads_data,
        filters={
            "query": q,
            "domain": domain,
            "confidence": confidence,
            "contact_type": contact_type,
        },
        total=len(leads_data),
    )

@views_bp.route("/leads/export")
def leads_export():
    return render_template("export.html")
