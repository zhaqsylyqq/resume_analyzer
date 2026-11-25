"""Sign-in (with the SEC-01 second step), sign-up (S1/S2), password reset links (SEC-01), sign-out."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..ratelimits import rate_limited
from ..security import is_safe_redirect
from ..services import storage
from ..settings import current as current_settings
from ..starter import create_starter_session
from . import passwords
from .blueprint import bp
from .limits import _client_ip, _limiter
from .passwords import (
    _DUMMY_HASH,
    fingerprint,
    hash_password,
    validate_email,
    validate_password,
    validate_username,
    verify_password,
)

MFA_PENDING_SECONDS = 300  # time to type the code after the password
RESET_LINK_LIFETIME = timedelta(hours=1)


@bp.route("/login", methods=["GET", "POST"])
def login():
    target = request.values.get("next")
    target = target if is_safe_redirect(target) else None
    if g.get("user") is not None:
        return redirect(target or url_for("core.index"))
    if request.method == "GET":
        return render_template("auth/login.html", username="", next=target, error=None)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    keys = (f"user:{username.lower()}", f"ip:{_client_ip()}")
    limiter = _limiter()
    if limiter.blocked(*keys):
        return render_template("auth/login.html", username=username, next=target, error="auth.error.locked"), 429

    db = storage.get_db()
    user = storage.get_user_by_name(db, username) if username else None
    ok = verify_password(user["password_hash"] if user else _DUMMY_HASH, password) and bool(user and user["active"])
    if not ok:
        limiter.fail(*keys)
        storage.audit(db, "login_failed", username=username[:64] or None, detail=_client_ip())
        status = 429 if limiter.blocked(*keys) else 401
        error = "auth.error.locked" if status == 429 else "auth.error.invalid"
        return render_template("auth/login.html", username=username, next=target, error=error), status

    limiter.reset(*keys)
    if passwords.needs_rehash(user["password_hash"]):
        storage.update_user(db, user["id"], password_hash=hash_password(password))
        user = storage.get_user(db, user["id"])
    if user["totp_secret"]:
        # SEC-01: the password was right; the session is not signed in until the code is.
        session.clear()
        session["mfa_user"] = user["id"]
        session["mfa_at"] = time.time()
        session["mfa_pw"] = fingerprint(user)
        if target:
            session["mfa_next"] = target
        return redirect(url_for("auth.login_verify"))
    _sign_in(db, user)
    return redirect(target or url_for("core.index"))


def _pending_mfa_user():
    user_id, started = session.get("mfa_user"), session.get("mfa_at", 0)
    if user_id is None or time.time() - started > MFA_PENDING_SECONDS:
        return None
    user = storage.get_user(storage.get_db(), user_id)
    if user is None or not user["active"] or not user["totp_secret"] or session.get("mfa_pw") != fingerprint(user):
        return None
    return user


@bp.route("/login/verify", methods=["GET", "POST"])
def login_verify():
    """SEC-01: second step — a code from the authenticator app or a one-time recovery code."""
    from ..services import totp

    if g.get("user") is not None:
        return redirect(url_for("core.index"))
    user = _pending_mfa_user()
    if user is None:
        session.clear()
        flash("mfa.expired", "warning")
        return redirect(url_for("auth.login"))
    if request.method == "GET":
        return render_template("auth/verify.html", error=None)

    keys = (f"user:{user['username'].lower()}", f"ip:{_client_ip()}")
    limiter = _limiter()
    if limiter.blocked(*keys):
        return render_template("auth/verify.html", error="auth.error.locked"), 429
    db = storage.get_db()
    code = request.form.get("code", "").strip()
    method = None
    step = totp.verify(storage.totp_secret(user) or "", code, user["totp_last_step"])
    if step is not None:
        storage.update_user(db, user["id"], totp_last_step=step)
        method = "2fa"
    else:
        remaining = totp.use_recovery_code(code, _recovery_hashes(user))
        if remaining is not None:
            storage.update_user(db, user["id"], recovery_codes=json.dumps(remaining))
            method = "recovery code"
    if method is None:
        limiter.fail(*keys)
        storage.audit(db, "login_failed", user, detail=f"{_client_ip()} wrong 2fa code")
        status = 429 if limiter.blocked(*keys) else 401
        return render_template("auth/verify.html", error="auth.error.locked" if status == 429 else "mfa.error.code"), status

    limiter.reset(*keys)
    target = session.get("mfa_next")
    target = target if is_safe_redirect(target) else None
    _sign_in(db, storage.get_user(db, user["id"]), method)
    if method == "recovery code":
        left = len(_recovery_hashes(storage.get_user(db, user["id"])))
        storage.audit(db, "mfa_recovery_used", user, detail=f"{_client_ip()} {left} code(s) left")
        flash("mfa.recovery_used", "warning")
    return redirect(target or url_for("core.index"))


def _recovery_hashes(user) -> list[str]:
    try:
        value = json.loads(user["recovery_codes"] or "[]")
    except ValueError:
        return []
    return [h for h in value if isinstance(h, str)]


def _sign_in(db, user, method: str | None = None) -> None:
    storage.update_user(db, user["id"], last_login_at=storage.now())
    storage.audit(db, "login", user, detail=_client_ip() + (f" ({method})" if method else ""))
    session.clear()  # new session on login: no fixation; keeps nothing from the anonymous one
    session.permanent = True
    session["user_id"] = user["id"]
    session["pw"] = fingerprint(user)


def registration_open() -> bool:
    return current_settings().signup_available


@bp.route("/register", methods=["GET", "POST"])
def register():
    """S1: classmates create their own recruiter account with the invite code."""
    if not registration_open():
        abort(404)
    if g.get("user") is not None:
        return redirect(url_for("core.index"))
    cfg = current_settings()
    form = {"username": request.form.get("username", "").strip(),
            "email": request.form.get("email", "").strip().lower()}
    render = lambda errors: render_template("auth/register.html", form=form, errors=errors,  # noqa: E731
                                           require_code=cfg.require_code)
    if request.method == "GET":
        return render({})

    limiter, ip_key = _limiter(), f"ip:{_client_ip()}"
    signups = current_app.extensions["signup_limiter"]
    if limiter.blocked(ip_key):
        return render({"code": "auth.error.locked"}), 429
    if signups.blocked(_client_ip()):
        return render({"username": "register.error.too_many"}), 429
    errors = {}
    if cfg.require_code:
        code = request.form.get("code", "").strip()
        if not hmac.compare_digest(code.encode(), cfg.invite_code.encode()):
            limiter.fail(ip_key)
            errors["code"] = "auth.error.locked" if limiter.blocked(ip_key) else "register.error.code"
    if error := validate_email(form["email"], required=True):
        errors["email"] = error
    elif storage.email_taken(storage.get_db(), form["email"]):
        errors["email"] = "auth.error.email_taken"
    password = request.form.get("password", "")
    db = storage.get_db()
    if error := validate_username(form["username"]):
        errors["username"] = error
    elif storage.get_user_by_name(db, form["username"]):
        errors["username"] = "auth.error.username_taken"
    if error := validate_password(password):
        errors["password"] = error
    elif password != request.form.get("password2", ""):
        errors["password2"] = "register.error.mismatch"
    if errors:
        status = 429 if errors.get("code") == "auth.error.locked" else 400
        return render(errors), status

    user_id = storage.create_user(db, form["username"], hash_password(password), "recruiter", form["email"])
    create_starter_session(db, user_id, current_app.config["STARTER_SESSION"])
    signups.record(_client_ip())
    user = storage.get_user(db, user_id)
    storage.audit(db, "register", user, detail=_client_ip())
    _sign_in(db, user)
    flash("register.welcome", "success")
    return redirect(url_for("core.index"))


# SEC-01: password reset by a one-time link -------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_link(db, user, created_by=None) -> str:
    """A one-time link valid for RESET_LINK_LIFETIME; only its hash is stored."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + RESET_LINK_LIFETIME).isoformat(timespec="seconds")
    storage.create_password_reset(db, _token_hash(token), user["id"], created_by["id"] if created_by else None, expires)
    return url_for("auth.reset_password", token=token, _external=True)


@bp.route("/reset/<token>", methods=["GET", "POST"])
@rate_limited("reset", 10, 900, by="ip", methods=("POST",))
def reset_password(token: str):
    """The user sets a new password. Two-factor sign-in stays on: a leaked link alone is not
    enough to take over an account protected by it."""
    db = storage.get_db()
    token_hash = _token_hash(token)
    limiter, ip_key = _limiter(), f"ip:{_client_ip()}"
    if limiter.blocked(ip_key):
        return render_template("auth/reset.html", valid=False, error="auth.error.locked"), 429
    reset = storage.get_password_reset(db, token_hash)
    if reset is None:
        limiter.fail(ip_key)  # guessing tokens is pointless, but do not offer unlimited tries
        storage.audit_once(db, "password_reset_failed", None, None, f"{_client_ip()} unknown or expired link")
        return render_template("auth/reset.html", valid=False, error="reset.error.invalid"), 404
    user = storage.get_user(db, reset["user_id"])
    if user is None or not user["active"]:
        storage.audit_once(db, "password_reset_failed", user, None, f"{_client_ip()} account switched off")
        return render_template("auth/reset.html", valid=False, error="reset.error.invalid"), 404
    if request.method == "GET":
        return render_template("auth/reset.html", valid=True, username=user["username"], error=None)
    password = request.form.get("password", "")
    error = validate_password(password)
    if not error and password != request.form.get("password2", ""):
        error = "register.error.mismatch"
    if error:
        return render_template("auth/reset.html", valid=True, username=user["username"], error=error), 400
    if not storage.use_password_reset(db, token_hash, hash_password(password)):
        storage.audit(db, "password_reset_failed", user, detail=f"{_client_ip()} link already used")
        return render_template("auth/reset.html", valid=False, error="reset.error.invalid"), 404
    storage.audit(db, "password_reset", user, detail=_client_ip())
    session.clear()
    flash("reset.done", "success")
    return redirect(url_for("auth.login"))


@bp.post("/logout")
def logout():
    storage.audit(storage.get_db(), "logout", g.user)
    session.clear()
    flash("auth.logged_out", "info")
    return redirect(url_for("auth.login"))
