"""S1 — usage statistics for admins: evidence of how many people used the service.

Everything is computed from `users`, `audit_log` and `resumes`; no AI call. Sign-ins,
sessions and exports are counted from the audit log, so sessions later removed by the
retention purge still count. Dates are grouped in local time (`STATS_UTC_OFFSET` hours).
"""
from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .export import _set_text_types, _text

PERIODS = (7, 30)
CHART_DAYS = 31
BRAND = "14213D"


@dataclass
class Period:
    days: int | None  # None = all time
    utc_offset: int
    now: datetime

    @property
    def since(self) -> str | None:
        """ISO UTC bound for created_at, or None for all time (local midnight N-1 days ago)."""
        if self.days is None:
            return None
        local_today = (self.now + timedelta(hours=self.utc_offset)).date()
        start_local = datetime.combine(local_today - timedelta(days=self.days - 1), datetime.min.time())
        return (start_local - timedelta(hours=self.utc_offset)).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")

    def where(self, column: str = "created_at") -> tuple[str, tuple]:
        return ("", ()) if self.since is None else (f" AND {column} >= ?", (self.since,))

    def local_day(self, column: str) -> str:
        return f"date({column}, '{self.utc_offset:+d} hours')"


def make_period(days: int | None, utc_offset: int, now: datetime | None = None) -> Period:
    return Period(days if days in PERIODS else None, utc_offset, now or datetime.now(timezone.utc))


def _count(db: sqlite3.Connection, sql: str, params: tuple) -> int:
    return int(db.execute(sql, params).fetchone()[0] or 0)


def summary(db: sqlite3.Connection, period: Period) -> dict[str, int]:
    w, p = period.where()
    wa, pa = period.where("analyzed_at")
    return {
        "users_total": _count(db, "SELECT COUNT(*) FROM users", ()),
        "users_new": _count(db, f"SELECT COUNT(*) FROM users WHERE 1=1{w}", p),
        "signed_in": _count(db, f"SELECT COUNT(DISTINCT user_id) FROM audit_log WHERE action = 'login' AND user_id IS NOT NULL{w}", p),
        "active": _count(db, f"SELECT COUNT(DISTINCT user_id) FROM audit_log WHERE action = 'session_create' AND user_id IS NOT NULL{w}", p),
        "sessions": _count(db, f"SELECT COUNT(*) FROM audit_log WHERE action = 'session_create'{w}", p),
        "resumes": _count(db, f"SELECT COUNT(*) FROM resumes WHERE status = 'done'{wa}", pa),
        "exports": _count(db, f"SELECT COUNT(*) FROM audit_log WHERE action = 'export'{w}", p),
        "logins": _count(db, f"SELECT COUNT(*) FROM audit_log WHERE action = 'login'{w}", p),
    }


def daily(db: sqlite3.Connection, period: Period) -> list[dict[str, Any]]:
    """One row per local day from the first event (or the period start) to today."""
    w, p = period.where()
    day = period.local_day("created_at")
    rows = db.execute(
        f"""
        SELECT {day} AS day,
               COUNT(DISTINCT CASE WHEN action = 'login' THEN user_id END) AS users,
               SUM(action = 'login') AS logins,
               SUM(action = 'session_create') AS sessions,
               SUM(action = 'export') AS exports,
               SUM(action = 'register') AS registrations
        FROM audit_log WHERE 1=1{w} GROUP BY day
        """,
        p,
    ).fetchall()
    by_day = {r["day"]: dict(r) for r in rows}
    today = (period.now + timedelta(hours=period.utc_offset)).date()
    if period.days is not None:
        first = today - timedelta(days=period.days - 1)
    elif by_day:
        first = min(date.fromisoformat(d) for d in by_day)
    else:
        first = today
    out = []
    current = first
    while current <= today:
        key = current.isoformat()
        row = by_day.get(key, {})
        out.append({"day": key, **{k: int(row.get(k) or 0) for k in ("users", "logins", "sessions", "exports", "registrations")}})
        current += timedelta(days=1)
    return out


def per_user(db: sqlite3.Connection, period: Period) -> list[dict[str, Any]]:
    w, p = period.where("a.created_at")
    wa, pa = period.where("r.analyzed_at")
    rows = db.execute(
        f"""
        SELECT u.id, u.username, u.email, u.role, u.active, u.created_at,
               (SELECT MIN(a.created_at) FROM audit_log a WHERE a.user_id = u.id AND a.action = 'login'{w}) AS first_login,
               (SELECT MAX(a.created_at) FROM audit_log a WHERE a.user_id = u.id AND a.action = 'login'{w}) AS last_login,
               (SELECT COUNT(*) FROM audit_log a WHERE a.user_id = u.id AND a.action = 'login'{w}) AS logins,
               (SELECT COUNT(*) FROM audit_log a WHERE a.user_id = u.id AND a.action = 'session_create'{w}) AS sessions,
               (SELECT COUNT(*) FROM audit_log a WHERE a.user_id = u.id AND a.action = 'export'{w}) AS exports,
               (SELECT COUNT(*) FROM resumes r JOIN sessions s ON s.id = r.session_id
                 WHERE s.owner_id = u.id AND r.status = 'done'{wa}) AS resumes
        FROM users u ORDER BY logins DESC, u.username COLLATE NOCASE
        """,
        p * 5 + pa,
    ).fetchall()
    return [dict(r) for r in rows]


def audit_rows(db: sqlite3.Connection, period: Period) -> list[sqlite3.Row]:
    w, p = period.where()
    return db.execute(f"SELECT * FROM audit_log WHERE 1=1{w} ORDER BY created_at, id", p).fetchall()


def report(db: sqlite3.Connection, period: Period) -> dict[str, Any]:
    days = daily(db, period)
    chart = days[-CHART_DAYS:]
    peak = max([d["users"] for d in chart] + [d["sessions"] for d in chart] + [1])
    return {"summary": summary(db, period), "daily": days, "chart": chart, "peak": peak, "users": per_user(db, period)}


def _local(value: str | None, offset: int) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value) + timedelta(hours=offset)
    except ValueError:
        return value
    return moment.strftime("%Y-%m-%d %H:%M")


def to_xlsx(db: sqlite3.Connection, period: Period, t: Callable[..., str]) -> bytes:
    data = report(db, period)
    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor=BRAND)

    def sheet(ws, header: list[str], rows: list[list[Any]], widths: list[int]) -> None:
        ws.append(header)
        for cell in ws[1]:
            cell.font, cell.fill = head_font, head_fill
        for row in rows:
            ws.append([v if isinstance(v, (int, float)) or v is None else _text(v) for v in row])
        for row in ws.iter_rows():
            _set_text_types(row)
        for i, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = "A2"

    s = data["summary"]
    ws = wb.active
    ws.title = _text(t("stats.sheet.summary"))[:31]
    period_label = t("stats.period.all") if period.days is None else t("stats.period.days", days=period.days)
    sheet(ws, [t("stats.metric"), t("stats.value")], [
        [t("stats.xlsx.period"), period_label],
        [t("stats.xlsx.generated"), _local(period.now.isoformat(timespec="seconds"), period.utc_offset)],
        [t("stats.users_total"), s["users_total"]], [t("stats.users_new"), s["users_new"]],
        [t("stats.signed_in"), s["signed_in"]], [t("stats.active"), s["active"]],
        [t("stats.logins"), s["logins"]], [t("stats.sessions"), s["sessions"]],
        [t("stats.resumes"), s["resumes"]], [t("stats.exports"), s["exports"]],
    ], [44, 22])

    sheet(wb.create_sheet(_text(t("stats.sheet.daily"))[:31]),
          [t("stats.col.day"), t("stats.col.users"), t("stats.logins"), t("stats.sessions"), t("stats.exports"),
           t("stats.col.registrations")],
          [[d["day"], d["users"], d["logins"], d["sessions"], d["exports"], d["registrations"]] for d in data["daily"]],
          [12, 16, 12, 14, 12, 14])

    sheet(wb.create_sheet(_text(t("stats.sheet.users"))[:31]),
          [t("auth.username"), t("auth.email"), t("admin.role"), t("stats.col.registered"), t("stats.col.first_login"),
           t("stats.col.last_login"), t("stats.logins"), t("stats.sessions"), t("stats.resumes"), t("stats.exports")],
          [[u["username"], u["email"] or "", t(f"role.{u['role']}"), _local(u["created_at"], period.utc_offset),
            _local(u["first_login"], period.utc_offset), _local(u["last_login"], period.utc_offset),
            u["logins"], u["sessions"], u["resumes"], u["exports"]] for u in data["users"]],
          [20, 28, 16, 18, 18, 18, 10, 10, 10, 10])

    sheet(wb.create_sheet(_text(t("stats.sheet.audit"))[:31]),
          [t("admin.audit.time"), t("auth.username"), t("admin.audit.action"), t("admin.audit.session"),
           t("admin.audit.detail")],
          [[_local(r["created_at"], period.utc_offset), r["username"] or "", t(f"audit.{r['action']}"), r["session_id"],
            r["detail"] or ""] for r in audit_rows(db, period)],
          [18, 20, 26, 10, 50])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
