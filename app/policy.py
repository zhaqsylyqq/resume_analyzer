"""APP-10: who may open which route — one table, checked before every request.

Every endpoint has exactly one rule:

    PUBLIC     anyone, signed in or not (sign-in, sign-up, reset links, health checks, static)
    SIGNED_IN  any signed-in user
    OWNER      a signed-in user who may see the screening session in the URL (`session_id`):
               its owner, or an admin
    ADMIN      admins only

A route missing from RULES is closed: 404 for everyone, and an error in the log. So a new
route cannot be published by mistake without deciding who may open it, and
tests/test_access_matrix.py walks every registered route against this table.

Refusals answer 404, never 403 (no hints that the page exists); a signed-in user refused is
recorded in the audit log (`access_denied`, SEC-03). Views keep their own checks
(`admin_required`, `load_session_or_404`) as a second line.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, NoReturn

from flask import abort, g, jsonify, redirect, request, url_for

from .i18n import translate
from .services import storage

PUBLIC = "public"
SIGNED_IN = "signed_in"
OWNER = "owner"
ADMIN = "admin"
LEVELS = (PUBLIC, SIGNED_IN, OWNER, ADMIN)

RULES: dict[str, str] = {
    "static": PUBLIC,
    # core
    "core.health": PUBLIC,
    "core.ready": PUBLIC,
    "core.set_language": PUBLIC,
    "core.index": SIGNED_IN,
    # sign-in, sign-up, reset links
    "auth.login": PUBLIC,
    "auth.login_verify": PUBLIC,
    "auth.register": PUBLIC,
    "auth.reset_password": PUBLIC,
    "auth.logout": SIGNED_IN,
    # own account
    "account.index": SIGNED_IN,
    "account.change_password": SIGNED_IN,
    "account.start_2fa": SIGNED_IN,
    "account.confirm_2fa": SIGNED_IN,
    "account.disable_2fa": SIGNED_IN,
    "account.new_recovery_codes": SIGNED_IN,
    # screening sessions
    "sessions.index": SIGNED_IN,
    "sessions.new": SIGNED_IN,
    "sessions.create": SIGNED_IN,
    "sessions.show": OWNER,
    "sessions.status": OWNER,
    "sessions.delete": OWNER,
    "screening.upload": OWNER,
    "screening.analyze": OWNER,
    "vacancies.create": OWNER,
    "vacancies.delete": OWNER,
    "vacancies.show": OWNER,
    "vacancies.set_status": OWNER,
    "vacancies.recheck_bias": OWNER,
    "vacancies.export": OWNER,
    "vacancies.export_xlsx": OWNER,
    "vacancies.export_pdf": OWNER,
    "vacancies.export_session_xlsx": OWNER,
    "vacancies.bias_check": SIGNED_IN,  # a vacancy text before any session exists
    # administration
    "auth.users": ADMIN,
    "auth.create_user": ADMIN,
    "auth.update_user": ADMIN,
    "auth.update_settings": ADMIN,
    "auth.stats": ADMIN,
    "auth.stats_xlsx": ADMIN,
    "auth.audit_log": ADMIN,
}

log = logging.getLogger(__name__)


def declare(endpoint: str, rule: str) -> None:
    """Give a route registered outside the main blueprints (an extension, a test) its rule."""
    if rule not in LEVELS:
        raise ValueError(f"unknown access rule {rule!r}")
    RULES[endpoint] = rule


def rule_for(endpoint: str | None) -> str | None:
    return RULES.get(endpoint) if endpoint else None


def is_public(endpoint: str | None) -> bool:
    return rule_for(endpoint) == PUBLIC


# Kept for older imports (app.auth.PUBLIC_ENDPOINTS); the table above is the source.
PUBLIC_ENDPOINTS = frozenset(e for e, r in RULES.items() if r == PUBLIC)


# Who is asking ---------------------------------------------------------------------------------

def is_admin() -> bool:
    user = g.get("user")
    return bool(user and user["role"] == "admin")


def owner_filter() -> Any:
    """Argument for storage.list_sessions: all sessions for admins, own sessions otherwise."""
    return storage.ALL_OWNERS if is_admin() else g.user["id"]


def can_access(session_row) -> bool:
    return is_admin() or (g.get("user") is not None and session_row["owner_id"] == g.user["id"])


def wants_json() -> bool:
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return request.is_json or best == "application/json" or request.headers.get("X-CSRF-Token") is not None


# Answers ---------------------------------------------------------------------------------------

def deny(session_id: int | None = None) -> NoReturn:
    """SEC-03: a signed-in user asked for something that is not theirs. Logged (once per
    10 minutes for the same user and address), then answered with 404: no hints."""
    if g.get("user") is not None:
        from .auth.limits import client_ip  # the auth package imports this module

        storage.audit_once(storage.get_db(), "access_denied", g.user, session_id,
                           f"{request.method} {request.path} {client_ip()}")
    abort(404)


def login_required_response():
    """Anonymous visitor on a page that needs sign-in: 401 for scripts, the sign-in page otherwise."""
    if wants_json():
        return jsonify(error="unauthorized", code=401, message=translate("auth.required")), 401
    target = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=target) if target and target != "/" else url_for("auth.login"))


def admin_required(view):
    """Second line for admin views (the table already says ADMIN)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            deny()  # do not reveal admin pages to recruiters
        return view(*args, **kwargs)

    return wrapped


def enforce():
    """before_request: apply the rule of the requested endpoint (after the user is loaded)."""
    endpoint = request.endpoint
    if endpoint is None:  # unknown URL: the 404 handler answers
        return None
    rule = RULES.get(endpoint)
    if rule is None:
        log.error("route %s has no access rule in app/policy.py: closed", endpoint)
        abort(404)
    if rule == PUBLIC:
        return None
    if g.get("user") is None:
        return login_required_response()
    if rule == ADMIN and not is_admin():
        deny()
    if rule == OWNER:
        session_id = (request.view_args or {}).get("session_id")
        session = storage.get_session(storage.get_db(), session_id) if session_id is not None else None
        if session is None:
            abort(404)
        if not can_access(session):
            deny(session_id)
    return None
