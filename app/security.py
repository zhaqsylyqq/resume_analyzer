"""CSRF protection, security headers, safe-redirect helpers and the HTTPS scheme behind a
trusted proxy (QW-04)."""
from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
from typing import Any, Callable, Iterable
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


def is_trusted_proxy(peer: str | None, proxies: Iterable[str]) -> bool:
    """Peer matches an address or a network (e.g. 172.16.0.0/12 for a Docker network)."""
    try:
        address = ipaddress.ip_address((peer or "").strip())
    except ValueError:
        return False
    for entry in proxies:
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def _cf_scheme(environ: dict[str, Any]) -> str:
    try:
        visitor = json.loads(environ.get("HTTP_CF_VISITOR") or "{}")
    except ValueError:
        return ""
    scheme = visitor.get("scheme") if isinstance(visitor, dict) else None
    return scheme.lower() if isinstance(scheme, str) else ""


class TrustedProxyScheme:
    """WSGI middleware: take the request scheme from X-Forwarded-Proto, but only when the
    direct peer is a trusted proxy (TRUSTED_PROXIES).

    Cloudflare Tunnel terminates HTTPS and talks plain HTTP to the app, so without this the
    app thinks every request is http: HSTS is never sent and external URLs say http://.
    Werkzeug's ProxyFix trusts the header from any peer; this checks the peer first, and it
    never rewrites REMOTE_ADDR (auth.client_ip already handles the visitor IP).

    waitress removes X-Forwarded-* headers unless it is told about the proxy itself, so the
    scheme is also read from Cloudflare's own `CF-Visitor: {"scheme":"https"}` header.
    """

    def __init__(self, wsgi_app: Callable[..., Any], proxies: Iterable[str]) -> None:
        self.wsgi_app = wsgi_app
        self.proxies = tuple(proxies)

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]):
        proto = environ.get("HTTP_X_FORWARDED_PROTO", "").split(",")[-1].strip().lower() or _cf_scheme(environ)
        if proto in ("http", "https") and is_trusted_proxy(environ.get("REMOTE_ADDR"), self.proxies):
            environ["wsgi.url_scheme"] = proto
        return self.wsgi_app(environ, start_response)


def init_security(app: Flask) -> None:
    app.wsgi_app = TrustedProxyScheme(app.wsgi_app, app.config.get("TRUSTED_PROXIES", ()))  # type: ignore[method-assign]
    app.before_request(_check_csrf)
    app.after_request(_set_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
