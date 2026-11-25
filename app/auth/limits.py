"""Limits on failed sign-ins and on sign-ups (A1, S2, APP-06), and the visitor's IP address."""
from __future__ import annotations

import ipaddress
from typing import Callable

from flask import current_app, request

from ..security import is_trusted_proxy
from ..services import ratelimit

MAX_SIGNUPS_PER_IP = 5
SIGNUP_WINDOW = 3600.0
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60.0
LOCKOUT = 15 * 60.0


class LoginLimiter:
    """Failures per key (username, IP): MAX_FAILURES within FAILURE_WINDOW lock the key for
    LOCKOUT. APP-06: counted in the database by default, so every process shares them."""

    def __init__(self, clock: Callable[[], float] | None = None, store: ratelimit.Store | None = None) -> None:
        self._limiter = ratelimit.Limiter(store or ratelimit.MemoryStore(), MAX_FAILURES, int(FAILURE_WINDOW),
                                          lockout=int(LOCKOUT), clock=clock)

    def blocked(self, *keys: str) -> bool:
        return self._limiter.blocked(*keys)

    def fail(self, *keys: str) -> None:
        self._limiter.hit(*keys)

    def reset(self, *keys: str) -> None:
        self._limiter.reset(*keys)


def _limiter() -> LoginLimiter:
    return current_app.extensions["login_limiter"]


class SignupLimiter:
    """S2: at most MAX_SIGNUPS_PER_IP successful sign-ups per IP per SIGNUP_WINDOW."""

    def __init__(self, clock: Callable[[], float] | None = None, store: ratelimit.Store | None = None) -> None:
        self._limiter = ratelimit.Limiter(store or ratelimit.MemoryStore(), MAX_SIGNUPS_PER_IP, int(SIGNUP_WINDOW),
                                          clock=clock)

    def blocked(self, ip: str) -> bool:
        return self._limiter.blocked(f"signup:{ip}")

    def record(self, ip: str) -> None:
        self._limiter.hit(f"signup:{ip}")


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def _trusted(peer: str, proxies: tuple[str, ...]) -> bool:
    return is_trusted_proxy(peer, proxies)


def client_ip() -> str:
    """Real visitor IP. Behind Cloudflare Tunnel every request comes from 127.0.0.1, so the
    limiters would treat all visitors as one person. Only a trusted proxy (loopback by
    default) may pass the visitor IP in CF-Connecting-IP or as the last X-Forwarded-For hop;
    from anyone else these headers are ignored, so they cannot be spoofed to dodge limits."""
    peer = request.remote_addr or "unknown"
    if not _trusted(peer, current_app.config.get("TRUSTED_PROXIES", ())):
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")
    return _valid_ip(request.headers.get("CF-Connecting-IP")) or _valid_ip(forwarded[-1]) or peer


_client_ip = client_ip
