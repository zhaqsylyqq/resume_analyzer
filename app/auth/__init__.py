"""Authentication: A1 (sign-in, roles), S1/S2 (sign-up), SEC-01 (2FA, reset links), SEC-03.

APP-10 split the former auth.py into:

    passwords.py  hashing, verification, the session fingerprint, input validation
    limits.py     limits on failed sign-ins and sign-ups, the visitor's IP behind the tunnel
    login.py      sign-in with the second step, sign-up, reset links, sign-out
    admin.py      users, sign-up settings, statistics, the audit log
    commands.py   create-admin, reset-2fa, reset-link

Who may open which route is decided in app/policy.py (one table, checked before every
request). The signed-in user is loaded on every request into `g.user`; the Flask session
keeps the user id and a fingerprint of the password hash and the 2FA secret, so changing a
password or switching 2FA signs other sessions out.

Names are re-exported here so `from app.auth import …` keeps working.
"""
from __future__ import annotations

import time  # noqa: F401 - tests patch app.auth.time.time (the time module is shared)
from datetime import timedelta

from flask import Flask, current_app, flash, g, jsonify, redirect, request, session, url_for

from .. import policy
from ..i18n import translate
from ..policy import (  # noqa: F401 - re-exported
    PUBLIC_ENDPOINTS,
    admin_required,
    can_access,
    deny,
    is_admin,
    owner_filter,
)
from ..policy import wants_json as _wants_json  # noqa: F401 - re-exported
from ..services import ratelimit, storage
from ..settings import current as current_settings  # noqa: F401 - re-exported
from . import admin as _admin  # noqa: F401 - registers the admin routes on bp
from .admin import AUDIT_DANGER, AUDIT_MAX_PAGE, AUDIT_PAGE_SIZE, ROLES, _audit_actions, _local_day_bound  # noqa: F401
from .blueprint import bp
from .commands import create_admin, reset_2fa, reset_link_command
from .limits import (  # noqa: F401 - re-exported
    FAILURE_WINDOW,
    LOCKOUT,
    MAX_FAILURES,
    MAX_SIGNUPS_PER_IP,
    SIGNUP_WINDOW,
    LoginLimiter,
    SignupLimiter,
    _client_ip,
    _limiter,
    client_ip,
)
from .login import (  # noqa: F401 - re-exported
    MFA_PENDING_SECONDS,
    RESET_LINK_LIFETIME,
    create_reset_link,
    registration_open,
)
from .passwords import (  # noqa: F401 - re-exported
    EMAIL_RE,
    MAX_EMAIL,
    MIN_PASSWORD,
    USERNAME_RE,
    _fingerprint,
    fingerprint,
    hash_password,
    validate_email,
    validate_password,
    validate_username,
    verify_password,
)

SESSION_LIFETIME = timedelta(hours=12)
# SEC-01: pages an admin without two-factor sign-in may still open when ADMIN_MFA_REQUIRED is on
MFA_SETUP_ENDPOINTS = frozenset({"account.index", "account.start_2fa", "account.confirm_2fa", "auth.logout"})


def _load_user() -> None:
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    user = storage.get_user(storage.get_db(), user_id)
    if user is None or not user["active"] or session.get("pw") != fingerprint(user):
        session.clear()
        return
    g.user = user


def _require_admin_mfa():
    """SEC-01: with ADMIN_MFA_REQUIRED, an admin must switch on two-factor sign-in first."""
    user = g.get("user")
    if (user is None or not current_app.config.get("ADMIN_MFA_REQUIRED") or user["role"] != "admin"
            or user["totp_secret"] or request.endpoint in MFA_SETUP_ENDPOINTS
            or policy.is_public(request.endpoint) or request.endpoint is None):
        return None
    if policy.wants_json():
        return jsonify(error="mfa_required", code=403, message=translate("mfa.required")), 403
    flash("mfa.required", "warning")
    return redirect(url_for("account.index"))


def init_auth(app: Flask) -> None:
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", SESSION_LIFETIME)
    store = ratelimit.store_for(app)
    app.extensions["rate_store"] = store
    app.extensions["login_limiter"] = LoginLimiter(store=store)
    app.extensions["signup_limiter"] = SignupLimiter(store=store)
    # Runs after the CSRF check registered by init_security: who is asking, may they, 2FA for admins.
    app.before_request(_load_user)
    app.before_request(policy.enforce)
    app.before_request(_require_admin_mfa)
    app.jinja_env.globals["is_admin"] = is_admin
    app.jinja_env.globals["registration_open"] = registration_open
    app.register_blueprint(bp)
    from ..account import bp as account_bp

    app.register_blueprint(account_bp)
    app.cli.add_command(create_admin)
    app.cli.add_command(reset_2fa)
    app.cli.add_command(reset_link_command)
