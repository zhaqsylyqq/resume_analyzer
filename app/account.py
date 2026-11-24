"""SEC-01 — the signed-in user's own account: change password, two-factor sign-in.

Two-factor sign-in (TOTP) is optional for everyone; with ADMIN_MFA_REQUIRED=1 admins are sent
here until they switch it on. Turning it on: the app shows a QR code / key, the user types a
code from the authenticator app to confirm, and gets 10 one-time recovery codes, shown once.
Switching it off or making new recovery codes asks for the password and a current code.
"""
from __future__ import annotations

import json

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .auth import _fingerprint, hash_password, validate_password, verify_password
from .ratelimits import rate_limited
from .services import storage, totp

bp = Blueprint("account", __name__, url_prefix="/account")

PENDING_KEY = "mfa_setup_secret"  # signed session cookie; only the user's own browser has it


def _page(status: int = 200, errors: dict[str, str] | None = None, recovery_codes: list[str] | None = None):
    user = g.user
    pending = session.get(PENDING_KEY)
    setup = None
    if pending and not user["totp_secret"]:
        uri = totp.provisioning_uri(pending, user["username"])
        setup = {"qr": totp.qr_svg_data_uri(uri), "key": totp.format_secret(pending)}
    remaining = len(json.loads(user["recovery_codes"] or "[]")) if user["totp_secret"] else 0
    return render_template("auth/account.html", setup=setup, errors=errors or {}, recovery_codes=recovery_codes,
                           recovery_left=remaining), status


def _reload_and_keep_signed_in() -> None:
    user = storage.get_user(storage.get_db(), g.user["id"])
    assert user is not None  # signed in a moment ago  # noqa: S101
    g.user = user
    session["pw"] = _fingerprint(user["password_hash"], user["totp_secret"])


@bp.get("")
def index():
    return _page()


@bp.post("/password")
@rate_limited("account-secret", 10, 900)
def change_password():
    user, db = g.user, storage.get_db()
    errors = {}
    if not verify_password(user["password_hash"], request.form.get("current", "")):
        errors["pw_current"] = "account.error.current"
    new = request.form.get("password", "")
    if error := validate_password(new):
        errors["password"] = error
    elif new != request.form.get("password2", ""):
        errors["password2"] = "register.error.mismatch"
    if errors:
        return _page(400, errors)
    storage.update_user(db, user["id"], password_hash=hash_password(new))
    storage.audit(db, "password_change", user)
    _reload_and_keep_signed_in()  # other sessions of this user are signed out
    flash("account.password_changed", "success")
    return redirect(url_for("account.index"))


@bp.post("/2fa/start")
def start_2fa():
    if not g.user["totp_secret"]:
        session[PENDING_KEY] = totp.generate_secret()
    return redirect(url_for("account.index") + "#mfa")


@bp.post("/2fa/confirm")
@rate_limited("account-secret", 10, 900)
def confirm_2fa():
    user, db = g.user, storage.get_db()
    secret = session.get(PENDING_KEY)
    if user["totp_secret"] or not secret:
        return redirect(url_for("account.index"))
    step = totp.verify(secret, request.form.get("code", ""), None)
    if step is None:
        return _page(400, {"code": "mfa.error.code"})
    codes = totp.generate_recovery_codes()
    storage.update_user(db, user["id"], totp_secret=secret, totp_last_step=step,
                        recovery_codes=json.dumps([totp.hash_recovery_code(c) for c in codes]))
    session.pop(PENDING_KEY, None)
    storage.audit(db, "mfa_enable", user)
    _reload_and_keep_signed_in()
    flash("mfa.enabled", "success")
    return _page(recovery_codes=codes)


def _check_password_and_code(errors: dict[str, str]) -> int | None:
    user = g.user
    if not verify_password(user["password_hash"], request.form.get("current", "")):
        errors["current"] = "account.error.current"
        return None
    step = totp.verify(user["totp_secret"], request.form.get("code", ""), user["totp_last_step"])
    if step is None:
        errors["code"] = "mfa.error.code"
    return step


@bp.post("/2fa/disable")
@rate_limited("account-secret", 10, 900)
def disable_2fa():
    user, db = g.user, storage.get_db()
    if not user["totp_secret"]:
        return redirect(url_for("account.index"))
    errors: dict[str, str] = {}
    if _check_password_and_code(errors) is None:
        return _page(400, errors)
    storage.update_user(db, user["id"], totp_secret=None, totp_last_step=None, recovery_codes=None)
    storage.audit(db, "mfa_disable", user)
    _reload_and_keep_signed_in()
    flash("mfa.disabled", "info")
    return redirect(url_for("account.index"))


@bp.post("/2fa/recovery")
@rate_limited("account-secret", 10, 900)
def new_recovery_codes():
    user, db = g.user, storage.get_db()
    if not user["totp_secret"]:
        return redirect(url_for("account.index"))
    errors: dict[str, str] = {}
    step = _check_password_and_code(errors)
    if step is None:
        return _page(400, errors)
    codes = totp.generate_recovery_codes()
    storage.update_user(db, user["id"], totp_last_step=step,
                        recovery_codes=json.dumps([totp.hash_recovery_code(c) for c in codes]))
    storage.audit(db, "mfa_recovery_codes", user)
    _reload_and_keep_signed_in()
    return _page(recovery_codes=codes)
