"""Service commands (APP-09): one entry point for maintenance tasks.

    flask --app app backup            consistent SQLite snapshot into <data>/backups
    flask --app app backup-offsite    encrypted copy into BACKUP_OFFSITE_DIR (QW-01)
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

import sqlite3
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
    for command in (backup_command, backup_offsite_command, restore_check_command, db_check_command,
                    generate_backup_key_command, config_check_command):
        app.cli.add_command(command)
