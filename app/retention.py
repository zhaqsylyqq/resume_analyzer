"""R1 — delete screening sessions older than RETENTION_DAYS.

Runs at app start, then at most once per RUN_INTERVAL on a later request (servers that are
never restarted still purge), and on demand with `flask --app app purge-old`.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from .services import jobs, storage

log = logging.getLogger(__name__)

RUN_INTERVAL = 24 * 3600.0
_lock = threading.Lock()


def purge(db, days: int, now: datetime | None = None) -> list[int]:
    """Delete sessions not changed for `days` days; return their ids. days <= 0 does nothing."""
    if days <= 0:
        return []
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
    deleted = []
    for row in storage.sessions_not_updated_since(db, cutoff):
        if jobs.is_running(row["id"]):
            continue  # analysis in progress: try again next run
        if storage.delete_session(db, row["id"]):
            storage.audit(db, "session_purge", session_id=row["id"], username="system",
                          detail=f"{row['title']} (older than {days} days)")
            deleted.append(row["id"])
    if deleted:
        log.info("Retention: deleted %d session(s) older than %d days", len(deleted), days)
    return deleted


def _run(app: Flask) -> None:
    db = storage.connect(app.config["DATABASE"])
    try:
        purge(db, app.config["RETENTION_DAYS"])
    except Exception:  # noqa: BLE001 - retention must never break the app
        log.exception("Retention run failed")
    finally:
        db.close()
        app.extensions["retention_last_run"] = time.monotonic()


def _maybe_run() -> None:
    app = current_app._get_current_object()
    last = app.extensions.get("retention_last_run", 0.0)
    if time.monotonic() - last < RUN_INTERVAL or not _lock.acquire(blocking=False):
        return
    try:
        _run(app)
    finally:
        _lock.release()


@click.command("purge-old")
@with_appcontext
@click.option("--days", type=int, default=None, help="Override RETENTION_DAYS for this run.")
def purge_old(days: int | None) -> None:
    """Delete screening sessions not changed for RETENTION_DAYS days."""
    days = current_app.config["RETENTION_DAYS"] if days is None else days
    if days <= 0:
        click.echo("Retention is off (RETENTION_DAYS=0); nothing deleted.")
        return
    db = storage.connect(current_app.config["DATABASE"])
    try:
        deleted = purge(db, days)
    finally:
        db.close()
    click.echo(f"Deleted {len(deleted)} session(s) not changed for {days} days.")


def init_retention(app: Flask) -> None:
    _run(app)
    app.before_request(_maybe_run)
    app.cli.add_command(purge_old)
