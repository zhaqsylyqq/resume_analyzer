"""Service commands (APP-09): one entry point for maintenance tasks.

    flask --app app backup            consistent SQLite snapshot into <data>/backups
    flask --app app backup-offsite    encrypted copy into BACKUP_OFFSITE_DIR (QW-01)
    flask --app app backup-auto       the same, only when the USB drive is in and the copy is old
    flask --app app restore-check     prove the newest encrypted copy can be restored
    flask --app app db-check          integrity check, table sizes, analysis queue
    flask --app app generate-backup-key
    flask --app app config-check      (APP-01)   purge-old (R1)   create-admin (A1)

They read the same validated configuration as the server (APP-01), so DATA_DIR, secrets from
*_FILE and production rules apply. The same commands run inside the container
(`docker compose exec app flask --app app backup`) and later as Kubernetes Jobs/CronJobs.
Exit code 0 on success, 1 with a one-line reason otherwise; output is plain ASCII so it
reads well in Windows Task Scheduler logs.
"""
from __future__ import annotations

import base64
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from .services import backup as backups
from .services import storage


def _database() -> Path:
    return Path(current_app.config["DATABASE"])


def _offsite_dir(out: Path | None) -> Path:
    if out is not None:
        return out
    folder = current_app.config.get("BACKUP_OFFSITE_DIR")
    if not folder:
        raise click.ClickException("set BACKUP_OFFSITE_DIR in .env or pass --out")
    return Path(folder)


def _key() -> bytes:
    try:
        return backups.load_key(current_app.config.get("BACKUP_KEY"))
    except backups.BackupError as exc:
        raise click.ClickException(str(exc)) from None


@click.command("backup")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Folder (default: <data>/backups).")
@click.option("--keep", type=click.IntRange(min=0), default=14, show_default=True, help="Copies to keep; 0 keeps all.")
@with_appcontext
def backup_command(out: Path | None, keep: int) -> None:
    """Consistent snapshot of the database, checked with integrity_check."""
    target_dir = out or Path(current_app.instance_path) / "backups"
    try:
        path = backups.backup(_database(), target_dir, keep)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise click.ClickException(f"backup failed: {exc}") from None
    click.echo(f"Backup written: {path} ({path.stat().st_size // 1024} KB)")


@click.command("backup-offsite")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Folder (default: BACKUP_OFFSITE_DIR).")
@click.option("--keep", type=click.IntRange(min=0), default=30, show_default=True, help="Copies to keep; 0 keeps all.")
@with_appcontext
def backup_offsite_command(out: Path | None, keep: int) -> None:
    """Encrypted copy of the database outside this disk (AES-256-GCM, BACKUP_KEY)."""
    folder, key = _offsite_dir(out), _key()
    try:
        path = backups.backup_offsite(_database(), folder, key, keep)
    except (backups.BackupError, OSError, sqlite3.Error, RuntimeError) as exc:
        raise click.ClickException(f"offsite backup failed: {exc}") from None
    click.echo(f"Encrypted backup written: {path} ({path.stat().st_size // 1024} KB)")


@click.command("restore-check")
@click.argument("file", type=click.Path(path_type=Path), required=False)
@click.option("--restore-to", type=click.Path(path_type=Path), default=None,
              help="Also write the decrypted database here (must not exist; stop the app first).")
@with_appcontext
def restore_check_command(file: Path | None, restore_to: Path | None) -> None:
    """Decrypt an encrypted copy (default: the newest), check it and count its rows."""
    key = _key()
    if file is None:
        copies = backups.list_backups(_offsite_dir(None))
        if not copies:
            raise click.ClickException("no encrypted copies found")
        file = copies[-1]
    try:
        counts = backups.check(file, key, restore_to)
    except (backups.BackupError, OSError, sqlite3.Error) as exc:
        raise click.ClickException(f"restore check failed: {exc}") from None
    click.echo(f"OK: {file.name}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    if restore_to:
        click.echo(f"Restored to {restore_to}")


@click.command("db-check")
@with_appcontext
def db_check_command() -> None:
    """Integrity check of the live database, row counts and the analysis queue."""
    path = _database()
    if not path.is_file():
        raise click.ClickException(f"database not found: {path}")
    db = storage.connect(str(path))
    try:
        result = db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise click.ClickException(f"integrity check failed: {result}")
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in backups.TABLES}  # noqa: S608 - fixed names
        statuses = dict(db.execute("SELECT status, COUNT(*) FROM resumes GROUP BY status").fetchall())
        waiting = len(storage.sessions_with_pending(db))
    finally:
        db.close()
    click.echo(f"Database: {path} ({path.stat().st_size // 1024} KB), integrity ok")
    click.echo("Rows: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    click.echo("Resumes by status: " + (", ".join(f"{k} {v}" for k, v in sorted(statuses.items())) or "none"))
    click.echo(f"Sessions waiting for analysis: {waiting}")


REMINDER_FILE = "last_reminder.txt"
REMIND_EVERY = timedelta(hours=20)


def _log(state_dir: Path, line: str) -> None:
    """Append to <data>/backups/offsite.log (the scheduled task runs without a console)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(state_dir / "offsite.log", "a", encoding="utf-8") as log:
        log.write(f"{stamp} {line}\n")


def notify_windows(title: str, text: str) -> bool:
    """A Windows notification (bottom-right corner). False when not on Windows or it failed."""
    if os.name != "nt":
        return False
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$x = $t.GetElementsByTagName('text');"
        f"$x.Item(0).AppendChild($t.CreateTextNode({_ps_quote(title)})) > $null;"
        f"$x.Item(1).AppendChild($t.CreateTextNode({_ps_quote(text)})) > $null;"
        "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        subprocess.run(  # noqa: S603 - fixed program, our own script
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],  # noqa: S607
            check=True, timeout=30, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _remind_due(state_dir: Path, now: datetime) -> bool:
    try:
        last = datetime.fromisoformat((state_dir / REMINDER_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    return now - last > REMIND_EVERY


@click.command("backup-auto")
@click.option("--max-age-hours", type=click.IntRange(min=1), default=20, show_default=True,
              help="Make a new copy when the newest one is older than this.")
@click.option("--remind-days", type=click.IntRange(min=1), default=3, show_default=True,
              help="Notify when there has been no verified copy for this long.")
@click.option("--keep", type=click.IntRange(min=0), default=30, show_default=True, help="Copies to keep.")
@with_appcontext
def backup_auto_command(max_age_hours: int, remind_days: int, keep: int) -> None:
    """For the scheduled task (every 30 min): back up when the USB drive is plugged in.

    Copies only when the drive is connected and the newest copy is older than --max-age-hours,
    verifies the copy, and shows a Windows notification at most once a day when there has been
    no verified copy for --remind-days or when a backup failed.
    """
    state_dir = Path(current_app.instance_path) / "backups"
    state_dir.mkdir(parents=True, exist_ok=True)
    folder = current_app.config.get("BACKUP_OFFSITE_DIR")
    if not folder:
        raise click.ClickException("set BACKUP_OFFSITE_DIR in .env")
    now = datetime.now(timezone.utc)
    result = backups.auto_backup(_database(), Path(folder), _key(), state_dir, max_age=timedelta(hours=max_age_hours),
                                 remind_after=timedelta(days=remind_days), keep=keep, now=now)
    if result.status in ("done", "failed"):
        _log(state_dir, result.message)  # "fresh" / "absent" every 30 minutes would flood the log
    if result.remind and _remind_due(state_dir, now):
        if result.status == "failed":
            shown = notify_windows("HR Screen: бэкап не удался", "Проверьте флешку. Подробности: instance\\backups\\offsite.log")
        else:
            days = (now - result.last_ok).days if result.last_ok else None
            ago = f"{days} дн." if days is not None else "ни разу"
            shown = notify_windows("HR Screen: вставьте флешку для бэкапа",
                                   f"Последняя проверенная копия базы: {ago} назад. Копия создастся сама за полчаса.")
        if shown:
            (state_dir / REMINDER_FILE).write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
        _log(state_dir, f"reminder: {result.message}" + ("" if shown else " (notification not shown)"))
    click.echo(f"{result.status.upper()}: {result.message}")
    if result.status == "failed":
        raise SystemExit(1)


@click.command("generate-backup-key")
def generate_backup_key_command() -> None:
    """Print a new BACKUP_KEY. Keep it in a password manager shared by two people."""
    click.echo(backups.generate_key())


@click.command("config-check")
def config_check_command() -> None:
    """Validate the configuration and print it (secrets shown only as set / not set)."""
    from .check_config import main

    raise SystemExit(main())


def init_cli(app: Flask) -> None:
    for command in (backup_command, backup_offsite_command, backup_auto_command, restore_check_command, db_check_command,
                    generate_backup_key_command, config_check_command):
        app.cli.add_command(command)
