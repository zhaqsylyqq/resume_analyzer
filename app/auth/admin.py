"""Admin pages: users, sign-up settings (S2), usage statistics (S1), the audit log (SEC-03)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from flask import Response, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .. import settings as app_settings
from ..i18n import translate
from ..policy import admin_required
from ..services import stats as usage_stats
from ..services import storage
from ..settings import current as current_settings
from ..starter import create_starter_session
from .blueprint import bp
from .login import RESET_LINK_LIFETIME, create_reset_link
from .passwords import _fingerprint, hash_password, validate_email, validate_password, validate_username

ROLES = ("recruiter", "admin")


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
            session["pw"] = _fingerprint(new_hash, storage.totp_secret(user))
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
    from ..i18n import TRANSLATIONS

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
