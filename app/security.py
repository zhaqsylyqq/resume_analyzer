"""CSRF protection, security headers and safe-redirect helpers."""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from flask import Flask, abort, request, session

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    ]
)


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _check_csrf() -> None:
    if request.method in SAFE_METHODS:
        return
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.headers.get(CSRF_HEADER) or request.form.get(CSRF_FORM_FIELD)
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        abort(400, description="csrf")


def _set_headers(response):
    headers = response.headers
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.is_secure:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def is_safe_redirect(target: str | None) -> bool:
    """Allow only same-site relative paths like '/sessions/1'."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and "\\" not in target


def init_security(app: Flask) -> None:
    app.before_request(_check_csrf)
    app.after_request(_set_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
