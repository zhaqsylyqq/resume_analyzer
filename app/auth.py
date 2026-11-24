"""A1 — login, logout, roles, login rate limiting and the create-admin CLI command.
S1 adds sign-up with an invite code (/register) and the admin statistics page; S2 makes
sign-up switchable by admins (open / with code / closed), asks for an email and limits
sign-ups per IP.

The signed-in user is loaded on every request into `g.user`. The Flask session stores the
user id and a fingerprint of the password hash, so changing a password or deactivating a
user signs them out on their next request. Every route requires login except PUBLIC_ENDPOINTS.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, NoReturn

import click
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.cli import with_appcontext

from . import settings as app_settings
from .i18n import translate
from .ratelimits import rate_limited
from .security import is_safe_redirect, is_trusted_proxy
from .services import ratelimit, storage
from .services import stats as usage_stats
from .starter import create_starter_session

bp = Blueprint("auth", __name__)

ROLES = ("recruiter", "admin")
USERNAME_RE = re.compile(r"^[\w.\-]{3,64}$", re.UNICODE)
MIN_PASSWORD = 10
SESSION_LIFETIME = timedelta(hours=12)
PUBLIC_ENDPOINTS = frozenset({"auth.login", "auth.login_verify", "auth.register", "auth.reset_password",
                              "core.health", "core.ready", "core.set_language", "static"})
# SEC-01: pages an admin without two-factor sign-in may still open when ADMIN_MFA_REQUIRED is on
MFA_SETUP_ENDPOINTS = frozenset({"account.index", "account.start_2fa", "account.confirm_2fa", "auth.logout"})
MFA_PENDING_SECONDS = 300  # time to type the code after the password
RESET_LINK_LIFETIME = timedelta(hours=1)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MAX_EMAIL = 254
MAX_SIGNUPS_PER_IP = 5
SIGNUP_WINDOW = 3600.0

MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60.0
LOCKOUT = 15 * 60.0

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
# A valid hash of a random password: verifying against it when the user does not exist
# costs the same time as a real check, so response time does not reveal usernames.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


# Passwords ----------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _fingerprint(password_hash: str, totp_secret: str | None = None) -> str:
    """Stored in the session: changing the password or switching two-factor sign-in on or off
    signs out every other session of that user (SEC-01)."""
    return hashlib.sha256(f"{password_hash}|{totp_secret or ''}".encode()).hexdigest()[:16]


def fingerprint(user) -> str:
    return _fingerprint(user["password_hash"], user["totp_secret"])


def validate_username(username: str) -> str | None:
    return None if USERNAME_RE.match(username or "") else "auth.error.username"


def validate_email(email: str, required: bool) -> str | None:
    if not email:
        return "auth.error.email_required" if required else None
    return None if len(email) <= MAX_EMAIL and EMAIL_RE.match(email) else "auth.error.email"


def validate_password(password: str) -> str | None:
    return None if len(password or "") >= MIN_PASSWORD else "auth.error.password_short"


# Rate limiting of failed logins ---------------------------------------------------------------

class LoginLimiter:
    """Failures per key (username, IP): MAX_FAILURES within FAILURE_WINDOW lock the key for
    LOCKOUT. APP-06: counted in the database by default, so every process shares them."""

    def __init__(self, clock: Callable[[], float] = time.time, store: ratelimit.Store | None = None) -> None:
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

    def __init__(self, clock: Callable[[], float] = time.time, store: ratelimit.Store | None = None) -> None:
        self._limiter = ratelimit.Limiter(store or ratelimit.MemoryStore(), MAX_SIGNUPS_PER_IP, int(SIGNUP_WINDOW),
                                          clock=clock)

    def blocked(self, ip: str) -> bool:
        return self._limiter.blocked(f"signup:{ip}")

    def record(self, ip: str) -> None:
        self._limiter.hit(f"signup:{ip}")


def current_settings() -> app_settings.AppSettings:
    if "app_settings" not in g:
        g.app_settings = app_settings.load(storage.get_db(), current_app.config)
    return g.app_settings


# Request hooks and decorators -------------------------------------------------------------

def is_admin() -> bool:
    user = g.get("user")
    return bool(user and user["role"] == "admin")


def owner_filter() -> Any:
    """Argument for storage.list_sessions: all sessions for admins, own sessions otherwise."""
    return storage.ALL_OWNERS if is_admin() else g.user["id"]


def can_access(session_row) -> bool:
    return is_admin() or (g.get("user") is not None and session_row["owner_id"] == g.user["id"])


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


def _wants_json() -> bool:
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return request.is_json or best == "application/json" or request.headers.get("X-CSRF-Token") is not None


def _require_login():
    if g.get("user") is not None:
        return _require_admin_mfa()
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if request.endpoint is None:  # unknown URL: let the 404 handler answer
        return None
    if _wants_json():
        return jsonify(error="unauthorized", code=401, message=translate("auth.required")), 401
    target = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=target) if target and target != "/" else url_for("auth.login"))


def _require_admin_mfa():
    """SEC-01: with ADMIN_MFA_REQUIRED, an admin must switch on two-factor sign-in first."""
    user = g.user
    if (not current_app.config.get("ADMIN_MFA_REQUIRED") or user["role"] != "admin" or user["totp_secret"]
            or request.endpoint in MFA_SETUP_ENDPOINTS or request.endpoint in PUBLIC_ENDPOINTS
            or request.endpoint is None):
        return None
    if _wants_json():
        return jsonify(error="mfa_required", code=403, message=translate("mfa.required")), 403
    flash("mfa.required", "warning")
    return redirect(url_for("account.index"))


def deny(session_id: int | None = None) -> NoReturn:
    """SEC-03: a signed-in user asked for something that is not theirs. Logged (once per
    10 minutes for the same user and address), then answered with 404: no hints."""
    if g.get("user") is not None:
        storage.audit_once(storage.get_db(), "access_denied", g.user, session_id,
                           f"{request.method} {request.path} {_client_ip()}")
    abort(404)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            deny()  # do not reveal admin pages to recruiters
        return view(*args, **kwargs)

    return wrapped


def init_auth(app: Flask) -> None:
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", SESSION_LIFETIME)
    store = ratelimit.store_for(app)
    app.extensions["rate_store"] = store
    app.extensions["login_limiter"] = LoginLimiter(store=store)
    app.extensions["signup_limiter"] = SignupLimiter(store=store)
    # Runs after the CSRF check registered by init_security.
    app.before_request(_load_user)
    app.before_request(_require_login)
    app.jinja_env.globals["is_admin"] = is_admin
    app.jinja_env.globals["registration_open"] = registration_open
    app.register_blueprint(bp)
    from .account import bp as account_bp

    app.register_blueprint(account_bp)
    app.cli.add_command(create_admin)
    app.cli.add_command(reset_2fa)
    app.cli.add_command(reset_link_command)


# Routes -------------------------------------------------------------------------------------

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
    if _hasher.check_needs_rehash(user["password_hash"]):
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
    from .services import totp

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
    step = totp.verify(user["totp_secret"], code, user["totp_last_step"])
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


# Admin: users and audit log -----------------------------------------------------------------

def _users_page(form=None, errors=None, settings_errors=None, status=200):
    db = storage.get_db()
    settings_form = current_settings()
    return render_template(
        "admin/users.html", users=storage.list_users(db), roles=ROLES,
        form=form or {"username": "", "email": "", "role": "recruiter"}, errors=errors or {},
        settings=settings_form, settings_errors=settings_errors or {},
    ), status


@bp.get("/admin/users")
@admin_required
def users():
    return _users_page()


@bp.post("/admin/users")
@admin_required
def create_user():
    db = storage.get_db()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "recruiter")
    errors = {}
    if error := validate_username(username):
        errors["username"] = error
    elif storage.get_user_by_name(db, username):
        errors["username"] = "auth.error.username_taken"
    if error := validate_email(email, required=False):
        errors["email"] = error
    elif email and storage.email_taken(db, email):
        errors["email"] = "auth.error.email_taken"
    if error := validate_password(password):
        errors["password"] = error
    if role not in ROLES:
        errors["role"] = "auth.error.role"
    if errors:
        return _users_page({"username": username, "email": email, "role": role}, errors, status=400)
    user_id = storage.create_user(db, username, hash_password(password), role, email)
    create_starter_session(db, user_id, current_app.config["STARTER_SESSION"])
    storage.audit(db, "user_create", g.user, detail=f"{username} ({role}) id={user_id}")
    flash("admin.user_created", "success")
    return redirect(url_for("auth.users"))


@bp.post("/admin/settings")
@admin_required
def update_settings():
    """S2: registration switches, invite code and daily resume limit."""
    code = request.form.get("invite_code", "").strip()
    errors = {}
    limit = _limit_field("daily_limit", current_settings().daily_limit, errors)
    ai_limit = _limit_field("ai_check_limit", current_settings().ai_check_limit, errors)
    new = app_settings.AppSettings(
        registration_open=request.form.get("registration_open") == "1",
        require_code=request.form.get("require_code") == "1",
        invite_code=code[:100],
        daily_limit=limit,
        ai_check_limit=ai_limit,
    )
    if new.registration_open and new.require_code and not new.invite_code:
        errors["invite_code"] = "settings.error.code"
    if errors:
        g.app_settings = new
        return _users_page(settings_errors=errors, status=400)
    db = storage.get_db()
    app_settings.save(db, new)
    g.pop("app_settings", None)
    mode = "closed" if not new.registration_open else ("code" if new.require_code else "open")
    storage.audit(db, "settings_update", g.user,
                  detail=f"registration={mode} daily_limit={new.daily_limit} ai_check_limit={new.ai_check_limit}")
    flash("settings.saved", "success")
    return redirect(url_for("auth.users") + "#registration")


def _limit_field(name: str, current: int, errors: dict[str, str]) -> int:
    """A 0..MAX_LIMIT number from the settings form; a form without the field keeps `current`."""
    raw = request.form.get(name)
    if raw is None:
        return current
    try:
        value = int(raw.strip())
        if not 0 <= value <= app_settings.MAX_LIMIT:
            raise ValueError
    except ValueError:
        errors[name] = "settings.error.limit"
        return current
    return value


@bp.post("/admin/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    db = storage.get_db()
    user = storage.get_user(db, user_id)
    if user is None:
        abort(404)
    action = request.form.get("action")
    is_self = user["id"] == g.user["id"]
    if action == "toggle":
        if is_self:
            flash("admin.error.self", "danger")
            return redirect(url_for("auth.users"))
        storage.update_user(db, user_id, active=0 if user["active"] else 1)
        detail = f"{user['username']}: {'deactivated' if user['active'] else 'activated'}"
    elif action == "role":
        role = request.form.get("role")
        if role not in ROLES or (is_self and role != "admin"):
            flash("admin.error.self" if is_self else "auth.error.role", "danger")
            return redirect(url_for("auth.users"))
        storage.update_user(db, user_id, role=role)
        detail = f"{user['username']}: role {role}"
    elif action == "password":
        password = request.form.get("password", "")
        if error := validate_password(password):
            flash(error, "danger")
            return redirect(url_for("auth.users"))
        new_hash = hash_password(password)
        storage.update_user(db, user_id, password_hash=new_hash)
        if is_self:  # keep the admin signed in after changing their own password
            session["pw"] = _fingerprint(new_hash, user["totp_secret"])
        detail = f"{user['username']}: password changed"
    elif action == "reset_link":
        # SEC-01: a one-time link the admin passes to the user (there is no e-mail sending).
        link = create_reset_link(db, user, g.user)
        storage.audit(db, "password_reset_link", g.user, detail=f"{user['username']}: link created")
        return render_template("admin/reset_link.html", target=user, link=link,
                               minutes=int(RESET_LINK_LIFETIME.total_seconds() // 60))
    elif action == "disable_2fa":
        if not user["totp_secret"]:
            return redirect(url_for("auth.users"))
        storage.update_user(db, user_id, totp_secret=None, totp_last_step=None, recovery_codes=None)
        if is_self:
            session["pw"] = _fingerprint(user["password_hash"], None)
        storage.audit(db, "mfa_reset", g.user, detail=f"{user['username']}: switched off by an admin")
        flash("admin.user_updated", "success")
        return redirect(url_for("auth.users"))
    else:
        abort(400)
    storage.audit(db, "user_update", g.user, detail=detail)
    flash("admin.user_updated", "success")
    return redirect(url_for("auth.users"))


def _stats_period() -> usage_stats.Period:
    try:
        days = int(request.args.get("days", ""))
    except ValueError:
        days = None
    return usage_stats.make_period(days, current_app.config["STATS_UTC_OFFSET"])


@bp.get("/admin/stats")
@admin_required
def stats():
    period = _stats_period()
    return render_template("admin/stats.html", data=usage_stats.report(storage.get_db(), period), period=period,
                           periods=usage_stats.PERIODS, offset=period.utc_offset)


@bp.get("/admin/stats.xlsx")
@admin_required
def stats_xlsx():
    period = _stats_period()
    db = storage.get_db()
    storage.audit(db, "stats_export", g.user, detail=f"days={period.days or 'all'}")
    data = usage_stats.to_xlsx(db, period, translate)
    stamp = period.now.strftime("%Y%m%d")
    response = Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response.headers["Content-Disposition"] = f'attachment; filename="usage-stats-{stamp}.xlsx"'
    response.headers["Cache-Control"] = "no-store"
    return response


AUDIT_PAGE_SIZE = 50
AUDIT_MAX_PAGE = 10_000
AUDIT_DANGER = {"login_failed", "session_delete", "access_denied", "mfa_failed", "mfa_reset",
                "password_change_failed", "password_reset_failed"}


def _audit_actions() -> list[str]:
    """Every action the log knows (each has a label `audit.<action>`)."""
    from .i18n import TRANSLATIONS

    return sorted(key.split(".", 1)[1] for key in TRANSLATIONS["ru"] if key.startswith("audit."))


def _local_day_bound(value: str | None, utc_offset: int, next_day: bool = False) -> str | None:
    """'2026-11-24' (a local date) -> ISO UTC moment of that local midnight (or the next one)."""
    try:
        day = date.fromisoformat((value or "").strip())
    except ValueError:
        return None
    start = datetime.combine(day + timedelta(days=1 if next_day else 0), datetime.min.time())
    return (start - timedelta(hours=utc_offset)).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


@bp.get("/admin/audit")
@admin_required
def audit_log():
    """SEC-03: the whole append-only log, newest first, page by page, with filters."""
    args, offset = request.args, current_app.config["STATS_UTC_OFFSET"]
    actions = _audit_actions()
    filters = {
        "action": args.get("action", "") if args.get("action", "") in actions else "",
        "user": args.get("user", "").strip()[:64],
        "from": args.get("from", "") if _local_day_bound(args.get("from"), offset) else "",
        "to": args.get("to", "") if _local_day_bound(args.get("to"), offset) else "",
    }
    try:
        page = min(max(int(args.get("page", 1)), 1), AUDIT_MAX_PAGE)
    except ValueError:
        page = 1
    rows = storage.search_audit(
        storage.get_db(), action=filters["action"] or None, username=filters["user"] or None,
        since=_local_day_bound(filters["from"], offset), until=_local_day_bound(filters["to"], offset, next_day=True),
        offset=(page - 1) * AUDIT_PAGE_SIZE, limit=AUDIT_PAGE_SIZE + 1,
    )
    active = {k: v for k, v in filters.items() if v}
    return render_template("admin/audit.html", entries=rows[:AUDIT_PAGE_SIZE], filters=filters, actions=actions,
                           page=page, has_older=len(rows) > AUDIT_PAGE_SIZE, active=active, danger=AUDIT_DANGER)


# CLI ------------------------------------------------------------------------------------------

@click.command("create-admin")
@with_appcontext
@click.option("--username", prompt=True, help="Login name of the new admin.")
@click.password_option("--password", help="Asked interactively (hidden, with confirmation) if omitted.")
def create_admin(username: str, password: str) -> None:
    """Create an admin user (there is no default admin or password)."""
    username = username.strip()
    db = storage.connect(current_app.config["DATABASE"])
    try:
        if validate_username(username):
            raise click.ClickException("Username: 3-64 letters, digits, '.', '_' or '-'.")
        if validate_password(password):
            raise click.ClickException(f"Password must be at least {MIN_PASSWORD} characters.")
        if storage.get_user_by_name(db, username):
            raise click.ClickException(f"User '{username}' already exists.")
        user_id = storage.create_user(db, username, hash_password(password), "admin")
        create_starter_session(db, user_id, current_app.config["STARTER_SESSION"])
        storage.audit(db, "user_create", username="cli", detail=f"{username} (admin) id={user_id}")
    finally:
        db.close()
    click.echo(f"Admin '{username}' created.")


@click.command("reset-2fa")
@with_appcontext
@click.argument("username")
def reset_2fa(username: str) -> None:
    """Switch off two-factor sign-in for a user who lost the phone and the recovery codes."""
    db = storage.connect(current_app.config["DATABASE"])
    try:
        user = storage.get_user_by_name(db, username.strip())
        if user is None:
            raise click.ClickException(f"User '{username}' not found.")
        storage.update_user(db, user["id"], totp_secret=None, totp_last_step=None, recovery_codes=None)
        storage.audit(db, "mfa_reset", username="cli", detail=f"{user['username']}: switched off from the command line")
    finally:
        db.close()
    click.echo(f"Two-factor sign-in switched off for '{user['username']}'. They should switch it on again.")


@click.command("reset-link")
@with_appcontext
@click.argument("username")
@click.option("--base-url", default="https://hrscreen.date", show_default=True, help="Address the user opens.")
def reset_link_command(username: str, base_url: str) -> None:
    """Print a one-time password reset link (valid 1 hour), e.g. for the only admin."""
    db = storage.connect(current_app.config["DATABASE"])
    try:
        user = storage.get_user_by_name(db, username.strip())
        if user is None:
            raise click.ClickException(f"User '{username}' not found.")
        with current_app.test_request_context(base_url=base_url):
            link = create_reset_link(db, user)
        storage.audit(db, "password_reset_link", username="cli", detail=f"{user['username']}: link created")
    finally:
        db.close()
    click.echo(link)
