"""APP-07: limits on how often one user (or one address) may call the expensive actions.

    @rate_limited("upload", 20, 60)            # 20 uploads a minute per signed-in user
    @rate_limited("reset", 10, 900, by="ip")   # 10 attempts in 15 minutes per address

Counters are shared by all processes (services/ratelimit.py, APP-06). Over the limit the
request gets 429 with Retry-After; HTML pages show the usual error page, fetch() calls a JSON
error. Limits are generous for a person and stop scripts and runaway loops — above all the
ones that would spend the AI balance.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import current_app, g, request
from werkzeug.exceptions import TooManyRequests

from .services import ratelimit


def _key(name: str, by: str) -> str:
    from .auth.limits import client_ip

    user = g.get("user")
    if by == "user" and user is not None:
        return f"rl:{name}:user:{user['id']}"
    return f"rl:{name}:ip:{client_ip()}"


def rate_limited(name: str, limit: int, window: int, by: str = "user",
                 methods: tuple[str, ...] | None = None) -> Callable[[Any], Any]:
    """`methods`: count only these (e.g. ("POST",) for a form whose GET just shows it)."""
    def decorate(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if current_app.config.get("RATE_LIMITS_ENABLED", True) and (
                    methods is None or request.method in methods):
                limiter = ratelimit.Limiter(current_app.extensions["rate_store"], limit, window)
                key = _key(name, by)
                if limiter.blocked(key):
                    raise TooManyRequests(retry_after=limiter.retry_after(key))
                limiter.hit(key)
            return view(*args, **kwargs)

        return wrapped

    return decorate
