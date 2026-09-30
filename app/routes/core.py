"""Home page, language switch and health check."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, redirect, render_template, request

from ..i18n import LANG_COOKIE
from ..security import is_safe_redirect
from ..services import storage

bp = Blueprint("core", __name__)


@bp.get("/")
def index():
    return render_template("index.html", sessions=storage.list_sessions(storage.get_db(), limit=6))


@bp.get("/lang/<code>")
def set_language(code: str):
    target = request.args.get("next")
    response = redirect(target if is_safe_redirect(target) else "/")
    if code in current_app.config["LANGUAGES"]:
        response.set_cookie(LANG_COOKIE, code, max_age=365 * 24 * 3600, samesite="Lax", httponly=True)
    return response


@bp.get("/healthz")
def health():
    return jsonify(status="ok")
