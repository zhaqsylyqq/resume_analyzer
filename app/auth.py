"""A1 — login, logout, roles, login rate limiting and the create-admin CLI command.
S1 adds sign-up with an invite code (/register) and the admin statistics page.

The signed-in user is loaded on every request into `g.user`. The Flask session stores the
user id and a fingerprint of the password hash, so changing a password or deactivating a
user signs them out on their next request. Every route requires login except PUBLIC_ENDPOINTS.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
from typing import Any, Callable

import click
from flask.cli import with_appcontext
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import (
    Blueprint, Flask, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, session,
    url_for,
)

from .i18n import translate
from .security import is_safe_redirect
from .services import stats as usage_stats
from .services import storage

bp = Blueprint("auth", __name__)

ROLES = ("recruiter", "admin")
USERNAME_RE = re.compile(r"^[\w.\-]{3,64}$", re.UNICODE)
MIN_PASSWORD = 10
SESSION_LIFETIME = timedelta(hours=12)
PUBLIC_ENDPOINTS = frozenset({"auth.login", "auth.register", "core.health", "core.set_language", "static"})

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


def _fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def validate_username(username: str) -> str | None:
    return None if USERNAME_RE.match(username or "") else "auth.error.username"


def validate_password(password: str) -> str | None:
    return None if len(password or "") >= MIN_PASSWORD else "auth.error.password_short"


# Rate limiting of failed logins ---------------------------------------------------------------

class LoginLimiter:
    """Failures per key (username, IP) in a sliding window; in memory, one process per DB."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked(self, *keys: str) -> bool:
        now = self.clock()
        with self._lock:
            return any(self._locked_until.get(k, 0) > now for k in keys)

    def fail(self, *keys: str) -> None:
        now = self.clock()
        with self._lock:
            for key in keys:
                window = self._failures[key]
                window.append(now)
                while window and window[0] < now - FAILURE_WINDOW:
                    window.popleft()
                if len(window) >= MAX_FAILURES:
                    self._locked_until[key] = now + LOCKOUT
                    window.clear()

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
                self._locked_until.pop(key, None)


def _limiter() -> LoginLimiter:
    return current_app.extensions["login_limiter"]


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
    if user is None or not user["active"] or session.get("pw") != _fingerprint(user["password_hash"]):
        session.clear()
        return
    g.user = user


def _wants_json() -> bool:
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return request.is_json or best == "application/json" or request.headers.get("X-CSRF-Token") is not None


def _require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or g.get("user") is not None:
        return None
    if request.endpoint is None:  # unknown URL: let the 404 handler answer
        return None
    if _wants_json():
        return jsonify(error="unauthorized", code=401, message=translate("auth.required")), 401
    target = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=target) if target and target != "/" else url_for("auth.login"))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            abort(404)  # do not reveal admin pages to recruiters
        return view(*args, **kwargs)

    return wrapped


def init_auth(app: Flask) -> None:
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", SESSION_LIFETIME)
    app.extensions["login_limiter"] = LoginLimiter()
    # Runs after the CSRF check registered by init_security.
    app.before_request(_load_user)
    app.before_request(_require_login)
    app.jinja_env.globals["is_admin"] = is_admin
    app.jinja_env.globals["registration_open"] = registration_open
    app.register_blueprint(bp)
    app.cli.add_command(create_admin)


# Routes -------------------------------------------------------------------------------------

def _client_ip() -> str:
    return request.remote_addr or "unknown"


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
    _sign_in(db, user)
    return redirect(target or url_for("core.index"))


def _sign_in(db, user) -> None:
    storage.update_user(db, user["id"], last_login_at=storage.now())
    storage.audit(db, "login", user, detail=_client_ip())
    session.clear()  # new session on login: no fixation; keeps nothing from the anonymous one
    session.permanent = True
    session["user_id"] = user["id"]
    session["pw"] = _fingerprint(user["password_hash"])


def registration_open() -> bool:
    return bool(current_app.config.get("INVITE_CODE"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    """S1: classmates create their own recruiter account with the invite code."""
    if not registration_open():
        abort(404)
    if g.get("user") is not None:
        return redirect(url_for("core.index"))
    form = {"username": request.form.get("username", "").strip()}
    if request.method == "GET":
        return render_template("auth/register.html", form=form, errors={})

    limiter, ip_key = _limiter(), f"ip:{_client_ip()}"
    if limiter.blocked(ip_key):
        return render_template("auth/register.html", form=form, errors={"code": "auth.error.locked"}), 429
    errors = {}
    code = request.form.get("code", "").strip()
    if not hmac.compare_digest(code.encode(), current_app.config["INVITE_CODE"].encode()):
        limiter.fail(ip_key)
        errors["code"] = "auth.error.locked" if limiter.blocked(ip_key) else "register.error.code"
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
        return render_template("auth/register.html", form=form, errors=errors), status

    user_id = storage.create_user(db, form["username"], hash_password(password), "recruiter")
    user = storage.get_user(db, user_id)
    storage.audit(db, "register", user, detail=_client_ip())
    _sign_in(db, user)
    flash("register.welcome", "success")
    return redirect(url_for("core.index"))


@bp.post("/logout")
def logout():
    storage.audit(storage.get_db(), "logout", g.user)
    session.clear()
    flash("auth.logged_out", "info")
    return redirect(url_for("auth.login"))


# Admin: users and audit log -----------------------------------------------------------------

@bp.get("/admin/users")
@admin_required
def users():
    return render_template("admin/users.html", users=storage.list_users(storage.get_db()), roles=ROLES,
                           form={"username": "", "role": "recruiter"}, errors={})


@bp.post("/admin/users")
@admin_required
def create_user():
    db = storage.get_db()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "recruiter")
    errors = {}
    if error := validate_username(username):
        errors["username"] = error
    elif storage.get_user_by_name(db, username):
        errors["username"] = "auth.error.username_taken"
    if error := validate_password(password):
        errors["password"] = error
    if role not in ROLES:
        errors["role"] = "auth.error.role"
    if errors:
        return render_template("admin/users.html", users=storage.list_users(db), roles=ROLES,
                               form={"username": username, "role": role}, errors=errors), 400
    user_id = storage.create_user(db, username, hash_password(password), role)
    storage.audit(db, "user_create", g.user, detail=f"{username} ({role}) id={user_id}")
    flash("admin.user_created", "success")
    return redirect(url_for("auth.users"))


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
            session["pw"] = _fingerprint(new_hash)
        detail = f"{user['username']}: password changed"
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


@bp.get("/admin/audit")
@admin_required
def audit_log():
    return render_template("admin/audit.html", entries=storage.list_audit(storage.get_db()))


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
        storage.audit(db, "user_create", username="cli", detail=f"{username} (admin) id={user_id}")
    finally:
        db.close()
    click.echo(f"Admin '{username}' created.")
