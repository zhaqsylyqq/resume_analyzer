"""Home page, language switch, liveness (/healthz) and readiness (/readyz) checks."""
from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app, jsonify, redirect, render_template, request

from ..auth import owner_filter
from ..i18n import LANG_COOKIE
from ..security import is_safe_redirect
from ..services import jobs, storage

bp = Blueprint("core", __name__)


@bp.get("/")
def index():
    return render_template("index.html", sessions=storage.list_sessions(storage.get_db(), limit=6, owner_id=owner_filter()))


@bp.get("/lang/<code>")
def set_language(code: str):
    target = request.args.get("next")
    response = redirect(target if target and is_safe_redirect(target) else "/")
    if code in current_app.config["LANGUAGES"]:
        response.set_cookie(LANG_COOKIE, code, max_age=365 * 24 * 3600, samesite="Lax", httponly=True)
    return response


@bp.get("/healthz")
def health():
    """Liveness: the process answers. Never touches the database, so a slow disk can't get
    a healthy process restarted."""
    return jsonify(status="ok")


@bp.get("/readyz")
def ready():
    """APP-08 readiness: send traffic here only when the database answers and the server is
    not shutting down. 503 tells a load balancer / Kubernetes to route around this copy."""
    if jobs.is_draining():
        return jsonify(status="shutting_down"), 503
    try:
        storage.get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        current_app.logger.exception("Readiness check: database unavailable")
        return jsonify(status="database_unavailable"), 503
    return jsonify(status="ready")
