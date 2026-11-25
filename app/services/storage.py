"""F3 — storage for screening sessions, vacancies, resumes and scores.

APP-02: the same SQL runs on SQLite (file path in DATABASE) and PostgreSQL (postgresql:// URL);
see services/db.py. SQLite gets its schema from SCHEMA below, PostgreSQL from Alembic (migrations/).

All SQL uses parameters. Demographics live in their own table with only what B2 needs
(gender, age) and are never joined into ranking or export queries. Users, session owners
and the audit log belong to A1 (login and roles).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from flask import Flask, current_app, g

from . import db as dbapi
from . import fieldcrypt
from .db import Database
from .schemas import CandidateProfile, FitAssessment

audit_logger = logging.getLogger("hrscreen.audit")

# Resume statuses
PENDING = "pending"
PROCESSING = "processing"
DONE = "done"
PARSE_FAILED = "parse_failed"
ANALYSIS_FAILED = "analysis_failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    language    TEXT NOT NULL DEFAULT 'ru',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vacancies (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    text        TEXT NOT NULL,
    bias_json   TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resumes (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    text         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,
    error        TEXT,
    profile_json TEXT,
    created_at   TEXT NOT NULL,
    analyzed_at  TEXT,
    raw_file     BLOB,
    UNIQUE (session_id, sha256)
);
CREATE TABLE IF NOT EXISTS demographics (
    resume_id   INTEGER PRIMARY KEY REFERENCES resumes(id) ON DELETE CASCADE,
    gender      TEXT NOT NULL DEFAULT 'unknown',
    age         INTEGER
);
CREATE TABLE IF NOT EXISTS scores (
    resume_id    INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    vacancy_id   INTEGER NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    score        INTEGER NOT NULL,
    verdict      TEXT NOT NULL,
    summary      TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (resume_id, vacancy_id)
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('recruiter', 'admin')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username    TEXT,
    action      TEXT NOT NULL,
    session_id  INTEGER,
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS candidate_status (
    vacancy_id  INTEGER NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    resume_id   INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    status      TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (vacancy_id, resume_id)
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user ON llm_usage(user_id, kind, created_at);
-- SEC-01: one-time password reset links created by an admin; only a hash of the token is kept
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);
-- APP-06: rate-limit counters shared by all processes (approximate sliding windows)
CREATE TABLE IF NOT EXISTS rate_counters (
    key          TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    count        INTEGER NOT NULL,
    PRIMARY KEY (key, window_start)
);
CREATE TABLE IF NOT EXISTS rate_locks (
    key    TEXT PRIMARY KEY,
    until  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_vacancies_session ON vacancies(session_id);
CREATE INDEX IF NOT EXISTS idx_resumes_session_status ON resumes(session_id, status);
CREATE INDEX IF NOT EXISTS idx_scores_vacancy ON scores(vacancy_id, score DESC);
"""


# SEC-03: the audit log is append-only (PostgreSQL: migration 0004 with the same rule).
AUDIT_GUARD = """
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only (SEC-03): UPDATE is not allowed'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only (SEC-03): DELETE is not allowed'); END;
"""


# SEC-02b: personal-data columns, encrypted at rest (services/fieldcrypt.py). The values are
# sealed on write here and opened on read here (or by the few queries that join them).
SEALED_TEXT = {
    "resumes": ("id", ("text", "filename", "profile_json")),
    "demographics": ("resume_id", ("gender", "age")),
    "users": ("id", ("totp_secret",)),
}
SEALED_BYTES = {"resumes": ("id", ("raw_file",))}


def seal(table: str, column: str, value: Any) -> Any:
    if value is None:
        return None
    return fieldcrypt.encrypt(str(value), f"{table}.{column}")


def unseal(table: str, column: str, value: Any) -> str | None:
    return fieldcrypt.decrypt(value, f"{table}.{column}")


def _open_resume(row: Any) -> dict[str, Any] | None:
    """A resume row with its personal columns decrypted (a plain dict)."""
    if row is None:
        return None
    out = {key: row[key] for key in row.keys()}
    for column in SEALED_TEXT["resumes"][1]:
        if column in out:
            out[column] = unseal("resumes", column, out[column])
    if out.get("raw_file") is not None:
        out["raw_file"] = fieldcrypt.decrypt_bytes(bytes(out["raw_file"]), "resumes.raw_file")
    return out


def totp_secret(user: Any) -> str | None:
    """SEC-02b: the user's 2FA secret in clear (stored encrypted)."""
    return unseal("users", "totp_secret", user["totp_secret"]) if user is not None else None


def seal_all(db: Database, batch: int = 200, plaintext: bool = False) -> dict[str, int]:
    """Encrypt every personal value that is still plaintext or sealed with an older key, with
    the current key. Idempotent and safe with several processes starting at once (each row is
    replaced only if it still holds the value that was read). Returns counts per column.

    plaintext=True does the opposite, for a rollback to a version before SEC-02b: every value
    is decrypted and stored in clear (the next start of this version encrypts it again)."""
    ring = fieldcrypt.keyring()
    # Text values to change: not under the current key (seal), or under any key (open).
    pattern = f"{fieldcrypt.PREFIX}%" if plaintext else f"{fieldcrypt.PREFIX}{ring.current_id}:%"
    test = "LIKE" if plaintext else "NOT LIKE"
    counts: dict[str, int] = {}
    for table, (key, columns) in SEALED_TEXT.items():
        for column in columns:
            done = 0
            # Names come from the fixed SEALED_TEXT map, values are bound.
            rows = db.execute(
                f"SELECT {key}, {column} FROM {table} WHERE {column} IS NOT NULL "  # noqa: S608
                f"AND CAST({column} AS TEXT) <> '' AND CAST({column} AS TEXT) {test} ?", (pattern,),
            ).fetchall()
            for i in range(0, len(rows), batch):
                with db:
                    for row_key, value in rows[i:i + batch]:
                        clear = unseal(table, column, value)
                        new_value = clear if plaintext else seal(table, column, clear)
                        done += db.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ? AND {column} = ?",  # noqa: S608
                                           (new_value, row_key, value)).rowcount or 0
            counts[f"{table}.{column}"] = done
    marker = fieldcrypt.BYTES_MAGIC + ring.current_id.encode()
    for table, (key, columns) in SEALED_BYTES.items():
        for column in columns:
            done = 0
            rows = db.execute(f"SELECT {key}, {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()  # noqa: S608
            with db:
                for row_key, value in rows:
                    data = bytes(value)
                    if (not data.startswith(fieldcrypt.BYTES_MAGIC)) if plaintext else data.startswith(marker):
                        continue
                    clear_bytes = fieldcrypt.decrypt_bytes(data, f"{table}.{column}")
                    new_bytes = clear_bytes if plaintext else fieldcrypt.encrypt_bytes(clear_bytes, f"{table}.{column}")
                    done += db.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ? AND {column} = ?",  # noqa: S608
                                       (new_bytes, row_key, value)).rowcount or 0
            counts[f"{table}.{column}"] = done
    return counts


def _rowid(row: Any) -> int:
    """The id from `INSERT … RETURNING id` (works on SQLite ≥ 3.35 and PostgreSQL)."""
    if row is None:
        raise RuntimeError("INSERT returned no row id")
    return int(row[0])


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(target: str) -> Database:
    """A SQLite file path or a postgresql:// URL (APP-02)."""
    return dbapi.connect(target)


def get_db() -> Database:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app: Flask) -> None:
    target = app.config["DATABASE"]
    if dbapi.is_postgres_url(target):
        from . import migrations as schema_migrations

        schema_migrations.upgrade(target)  # Alembic, under an advisory lock (several replicas may start at once)
    db = connect(target)
    try:
        if not db.is_postgres:
            db.raw.executescript(SCHEMA)
            _migrate(db)
            db.raw.executescript(AUDIT_GUARD)
        # Work interrupted by a restart goes back to the queue. With separate worker processes
        # (APP-04) a web restart must not take resumes away from running workers: they requeue
        # only stale ones (storage.requeue_stale).
        if app.config.get("WORKER_MODE", "inline") == "inline":
            db.execute("UPDATE resumes SET status = ? WHERE status = ?", (PENDING, PROCESSING))
        db.commit()
        # SEC-02b: plaintext from before the change, or values under an older key -> current key
        sealed = seal_all(db)
        if any(sealed.values()):
            logging.getLogger(__name__).info("Encrypted personal data with the current key: %s",
                                             ", ".join(f"{k}={v}" for k, v in sealed.items() if v))
    finally:
        db.close()
    app.teardown_appcontext(close_db)


def _migrate(db: Database) -> None:
    """Additive migrations for databases created by earlier versions."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
    user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
    if "email" not in user_columns:  # S2
        db.execute("ALTER TABLE users ADD COLUMN email TEXT")
    for column, kind in (("totp_secret", "TEXT"), ("totp_last_step", "INTEGER"), ("recovery_codes", "TEXT")):
        if column not in user_columns:  # SEC-01: two-factor sign-in
            db.execute(f"ALTER TABLE users ADD COLUMN {column} {kind}")
    resume_columns = {row["name"] for row in db.execute("PRAGMA table_info(resumes)")}
    if "started_at" not in resume_columns:  # Q1: time per resume for the ETA
        db.execute("ALTER TABLE resumes ADD COLUMN started_at TEXT")
    if "raw_file" not in resume_columns:  # APP-05: uploaded file waiting for a worker to parse it
        db.execute("ALTER TABLE resumes ADD COLUMN raw_file BLOB")
    if "owner_id" not in columns:
        # Sessions created before login existed have no owner: only admins see them.
        db.execute("ALTER TABLE sessions ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
    db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id, updated_at DESC)")


# Sessions ---------------------------------------------------------------------------------

def create_session(
    db: Database, title: str, language: str, vacancies: Iterable[tuple[str, str]],
    owner_id: int | None = None,
) -> int:
    stamp = now()
    with db:
        session_id = _rowid(db.execute(
            "INSERT INTO sessions (title, language, created_at, updated_at, owner_id) VALUES (?, ?, ?, ?, ?) RETURNING id",
            (title, language, stamp, stamp, owner_id),
        ).fetchone())
        for vacancy_title, text in vacancies:
            db.execute(
                "INSERT INTO vacancies (session_id, title, text, created_at) VALUES (?, ?, ?, ?)",
                (session_id, vacancy_title, text, stamp),
            )
    return session_id


def touch_session(db: Database, session_id: int) -> None:
    db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now(), session_id))


def get_session(db: Database, session_id: int) -> Any | None:
    return db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


ALL_OWNERS = object()  # sentinel: no owner filter (admins)


def list_sessions(db: Database, limit: int = 100, owner_id: Any = ALL_OWNERS) -> list[Any]:
    """Newest first. `owner_id` limits the list to one user's sessions; admins pass ALL_OWNERS."""
    where, params = ("", ()) if owner_id is ALL_OWNERS else ("WHERE s.owner_id = ?", (owner_id,))
    # `where` is one of two fixed literals; the owner id is bound.
    return db.execute(
        f"""
        SELECT s.*, u.username AS owner_name,
               (SELECT COUNT(*) FROM vacancies v WHERE v.session_id = s.id) AS vacancy_count,
               (SELECT COUNT(*) FROM resumes r WHERE r.session_id = s.id) AS resume_count,
               (SELECT COUNT(*) FROM resumes r WHERE r.session_id = s.id AND r.status = 'done') AS done_count
        FROM sessions s LEFT JOIN users u ON u.id = s.owner_id
        {where}
        ORDER BY s.updated_at DESC, s.id DESC LIMIT ?
        """,  # noqa: S608
        (*params, limit),
    ).fetchall()


def sessions_not_updated_since(db: Database, cutoff_iso: str) -> list[Any]:
    """Sessions whose last change is older than the cutoff (R1 retention)."""
    return db.execute(
        "SELECT id, title, updated_at FROM sessions WHERE updated_at < ? ORDER BY updated_at", (cutoff_iso,)
    ).fetchall()


def delete_session(db: Database, session_id: int) -> bool:
    with db:
        return db.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0


# Vacancies --------------------------------------------------------------------------------

def add_vacancy(db: Database, session_id: int, title: str, text: str) -> int:
    with db:
        vacancy_id = _rowid(db.execute(
            "INSERT INTO vacancies (session_id, title, text, created_at) VALUES (?, ?, ?, ?) RETURNING id",
            (session_id, title, text, now()),
        ).fetchone())
        touch_session(db, session_id)
    return vacancy_id


def delete_vacancy(db: Database, session_id: int, vacancy_id: int) -> bool:
    """S3: remove one vacancy and its scores (cascade); resumes stay in the session."""
    with db:
        deleted = db.execute("DELETE FROM vacancies WHERE id = ? AND session_id = ?", (vacancy_id, session_id)).rowcount > 0
        if deleted:
            touch_session(db, session_id)
    return deleted


def list_vacancies(db: Database, session_id: int) -> list[Any]:
    return db.execute("SELECT * FROM vacancies WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()


def get_vacancy(db: Database, session_id: int, vacancy_id: int) -> Any | None:
    return db.execute(
        "SELECT * FROM vacancies WHERE id = ? AND session_id = ?", (vacancy_id, session_id)
    ).fetchone()


def save_bias_report(db: Database, vacancy_id: int, report: dict[str, Any]) -> None:
    with db:
        db.execute("UPDATE vacancies SET bias_json = ? WHERE id = ?", (json.dumps(report, ensure_ascii=False), vacancy_id))


# Resumes ----------------------------------------------------------------------------------

def resume_exists(db: Database, session_id: int, sha256: str) -> bool:
    row = db.execute("SELECT 1 FROM resumes WHERE session_id = ? AND sha256 = ?", (session_id, sha256)).fetchone()
    return row is not None


def add_resume(
    db: Database, session_id: int, filename: str, sha256: str, text: str, status: str, error: str | None = None,
    raw_file: bytes | None = None,
) -> int | None:
    """Insert a resume; returns None if the same file is already in the session.

    APP-05: with `raw_file` the text is extracted later by a worker (status stays pending).
    """
    with db:
        row = db.execute(
            """INSERT INTO resumes (session_id, filename, sha256, text, status, error, created_at, raw_file)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (session_id, sha256) DO NOTHING RETURNING id""",
            (session_id, seal("resumes", "filename", filename), sha256, seal("resumes", "text", text), status, error,
             now(), fieldcrypt.encrypt_bytes(raw_file, "resumes.raw_file")),
        ).fetchone()
        if row is None:
            return None
        touch_session(db, session_id)
    return _rowid(row)


def raw_file(row: Any) -> bytes | None:
    """The uploaded file still waiting to be parsed (APP-05), as bytes on both engines (decrypted)."""
    value = row["raw_file"]
    return fieldcrypt.decrypt_bytes(bytes(value), "resumes.raw_file") if value is not None else None


def save_parsed(db: Database, resume_id: int, text: str | None, error: str | None) -> None:
    """APP-05: store the extracted text (or the parse error) and drop the uploaded file: the
    original document (it may hold a photo and contacts) is not kept once the text exists."""
    if error:
        from .. import metrics

        metrics.RESUMES_FINISHED.labels(PARSE_FAILED, error).inc()
    with db:
        if error:
            db.execute(
                "UPDATE resumes SET text = '', status = ?, error = ?, raw_file = NULL, analyzed_at = ? WHERE id = ?",
                (PARSE_FAILED, error, now(), resume_id),
            )
        else:
            db.execute("UPDATE resumes SET text = ?, raw_file = NULL WHERE id = ?", (seal("resumes", "text", text or ""), resume_id))


def list_resumes(db: Database, session_id: int) -> list[Any]:
    rows = db.execute(
        "SELECT id, filename, status, error, created_at, analyzed_at, profile_json FROM resumes "
        "WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [_open_resume(row) for row in rows]


def get_resume(db: Database, resume_id: int) -> Any | None:
    """The resume with its text, file name, profile and uploaded file decrypted."""
    return _open_resume(db.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone())


def claim_pending(db: Database, session_id: int, limit: int = 1000) -> list[int]:
    """Atomically move pending resumes to processing and return their ids.

    Two workers claiming at the same time (Q1 worker pool) never get the same resume:
    PostgreSQL locks the chosen rows (FOR UPDATE SKIP LOCKED: a second worker takes the next
    ones instead of waiting); SQLite takes the write lock before reading (BEGIN IMMEDIATE).
    """
    if db.is_postgres:
        with db:
            rows = db.execute(
                """UPDATE resumes SET status = ?, started_at = ? WHERE id IN (
                       SELECT id FROM resumes WHERE session_id = ? AND status = ?
                       ORDER BY id LIMIT ? FOR UPDATE SKIP LOCKED)
                   RETURNING id""",
                (PROCESSING, now(), session_id, PENDING, limit),
            ).fetchall()
        return sorted(row[0] for row in rows)
    db.execute("BEGIN IMMEDIATE")
    try:
        ids = [
            row["id"]
            for row in db.execute(
                "SELECT id FROM resumes WHERE session_id = ? AND status = ? ORDER BY id LIMIT ?",
                (session_id, PENDING, limit),
            )
        ]
        stamp = now()
        db.executemany("UPDATE resumes SET status = ?, started_at = ? WHERE id = ? AND status = ?",
                       [(PROCESSING, stamp, i, PENDING) for i in ids])
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return ids


def session_has_work(db: Database, session_id: int) -> bool:
    """APP-04: pending or processing resumes in this session (worker processes may be busy)."""
    return db.execute(
        "SELECT 1 FROM resumes WHERE session_id = ? AND status IN (?, ?) LIMIT 1", (session_id, PENDING, PROCESSING)
    ).fetchone() is not None


def requeue_stale(db: Database, cutoff_iso: str) -> int:
    """APP-04: resumes left in processing by a worker that died before this time go back to the queue."""
    with db:
        return db.execute(
            "UPDATE resumes SET status = ? WHERE status = ? AND (started_at IS NULL OR started_at < ?)",
            (PENDING, PROCESSING, cutoff_iso),
        ).rowcount


def sessions_with_pending(db: Database) -> list[int]:
    """QW-05: sessions that still have resumes waiting for analysis (oldest session first)."""
    return [row["session_id"] for row in db.execute(
        "SELECT DISTINCT session_id FROM resumes WHERE status = ? ORDER BY session_id", (PENDING,))]


def average_analysis_seconds(db: Database, sample: int = 20) -> float | None:
    """Mean analysis time of the last `sample` finished resumes in any session (Q1)."""
    rows = db.execute(
        """SELECT started_at, analyzed_at FROM resumes
           WHERE status = ? AND started_at IS NOT NULL AND analyzed_at IS NOT NULL
           ORDER BY analyzed_at DESC LIMIT ?""",
        (DONE, sample),
    ).fetchall()
    seconds = []
    for row in rows:  # computed here: date arithmetic differs between SQLite and PostgreSQL
        try:
            seconds.append((datetime.fromisoformat(row[1]) - datetime.fromisoformat(row[0])).total_seconds())
        except (TypeError, ValueError):
            continue
    return sum(seconds) / len(seconds) if seconds else None


def requeue_failed(db: Database, session_id: int) -> int:
    with db:
        return db.execute(
            "UPDATE resumes SET status = ?, error = NULL WHERE session_id = ? AND status = ?",
            (PENDING, session_id, ANALYSIS_FAILED),
        ).rowcount


def requeue_unscored(db: Database, session_id: int) -> int:
    """Queue analyzed resumes that are missing a score for some vacancy (e.g. a new vacancy)."""
    with db:
        return db.execute(
            """
            UPDATE resumes SET status = ? WHERE session_id = ? AND status = ? AND EXISTS (
                SELECT 1 FROM vacancies v WHERE v.session_id = resumes.session_id AND NOT EXISTS (
                    SELECT 1 FROM scores s WHERE s.resume_id = resumes.id AND s.vacancy_id = v.id))
            """,
            (PENDING, session_id, DONE),
        ).rowcount


def set_resume_status(db: Database, resume_id: int, status: str, error: str | None = None) -> None:
    if status in (DONE, ANALYSIS_FAILED):  # OBS-01: a resume left the queue
        from .. import metrics

        metrics.RESUMES_FINISHED.labels(status, error or "").inc()
    with db:
        db.execute(
            "UPDATE resumes SET status = ?, error = ?, analyzed_at = CASE WHEN ? THEN ? ELSE analyzed_at END WHERE id = ?",
            (status, error, status in (DONE, ANALYSIS_FAILED), now(), resume_id),
        )


def save_profile(db: Database, resume_id: int, profile: CandidateProfile) -> None:
    public = profile.model_dump(exclude={"demographics"})
    with db:
        db.execute(
            "UPDATE resumes SET profile_json = ? WHERE id = ?",
            (seal("resumes", "profile_json", json.dumps(public, ensure_ascii=False)), resume_id),
        )
        db.execute(
            "INSERT INTO demographics (resume_id, gender, age) VALUES (?, ?, ?) "
            "ON CONFLICT (resume_id) DO UPDATE SET gender = excluded.gender, age = excluded.age",
            (resume_id, seal("demographics", "gender", profile.demographics.gender),
             seal("demographics", "age", _age(profile))),
        )


def _age(profile: CandidateProfile) -> int | None:
    demo = profile.demographics
    if demo.age:
        return demo.age
    if demo.birth_year:
        return datetime.now(timezone.utc).year - demo.birth_year
    return None


def load_profile(row: Any) -> CandidateProfile | None:
    raw = unseal("resumes", "profile_json", row["profile_json"])  # rows from get_resume are already open
    return CandidateProfile.model_validate_json(raw) if raw else None


def unscored_vacancies(db: Database, resume_id: int) -> list[Any]:
    return db.execute(
        """
        SELECT v.* FROM vacancies v JOIN resumes r ON r.session_id = v.session_id
        WHERE r.id = ? AND NOT EXISTS (SELECT 1 FROM scores s WHERE s.resume_id = r.id AND s.vacancy_id = v.id)
        ORDER BY v.id
        """,
        (resume_id,),
    ).fetchall()


def save_scores(db: Database, resume_id: int, assessments: Iterable[FitAssessment]) -> None:
    stamp = now()
    with db:
        # A vacancy deleted while this resume was being scored is skipped (no FK error).
        db.executemany(
            """INSERT INTO scores (resume_id, vacancy_id, score, verdict, summary, details_json, created_at)
               SELECT ?, ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM vacancies WHERE id = ?)
               ON CONFLICT (resume_id, vacancy_id) DO UPDATE SET
                 score = excluded.score, verdict = excluded.verdict, summary = excluded.summary,
                 details_json = excluded.details_json, created_at = excluded.created_at""",
            [
                (
                    resume_id,
                    a.vacancy_id,
                    a.score,
                    a.verdict,
                    a.summary,
                    json.dumps(a.model_dump(exclude={"vacancy_id", "score", "verdict", "summary"}), ensure_ascii=False),
                    stamp,
                    a.vacancy_id,  # for the EXISTS check (plain '?': numbered '?2' is deprecated with a sequence)
                )
                for a in assessments
            ],
        )


def progress(db: Database, session_id: int) -> dict[str, int]:
    counts = {PENDING: 0, PROCESSING: 0, DONE: 0, PARSE_FAILED: 0, ANALYSIS_FAILED: 0}
    for row in db.execute(
        "SELECT status, COUNT(*) AS n FROM resumes WHERE session_id = ? GROUP BY status", (session_id,)
    ):
        counts[row["status"]] = row["n"]
    counts["total"] = sum(counts.values())
    return counts


# Users and audit log (A1) -------------------------------------------------------------------

def create_user(db: Database, username: str, password_hash: str, role: str,
                email: str | None = None) -> int:
    with db:
        return _rowid(db.execute(
            "INSERT INTO users (username, password_hash, role, created_at, email) VALUES (?, ?, ?, ?, ?) RETURNING id",
            (username, password_hash, role, now(), email or None),
        ).fetchone())


def get_settings(db: Database) -> dict[str, str]:
    return {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}


def save_settings(db: Database, values: dict[str, str]) -> None:
    stamp = now()
    with db:
        db.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [(k, v, stamp) for k, v in values.items()],
        )


def resumes_added_since(db: Database, owner_id: int, since_iso: str) -> int:
    """S2: resumes accepted for analysis (not parse failures) in the owner's sessions since a time."""
    return int(db.execute(
        """
        SELECT COUNT(*) FROM resumes r JOIN sessions s ON s.id = r.session_id
        WHERE s.owner_id = ? AND r.created_at >= ? AND r.status != ?
        """,
        (owner_id, since_iso, PARSE_FAILED),
    ).fetchone()[0])


# AI calls outside resume analysis (QW-02) ------------------------------------------------

AI_BIAS_CHECK = "bias_check"


def record_llm_use(db: Database, user_id: int | None, kind: str) -> None:
    with db:
        db.execute("INSERT INTO llm_usage (user_id, kind, created_at) VALUES (?, ?, ?)", (user_id, kind, now()))


def llm_uses_since(db: Database, user_id: int, kind: str, since_iso: str) -> int:
    return int(db.execute(
        "SELECT COUNT(*) FROM llm_usage WHERE user_id = ? AND kind = ? AND created_at >= ?", (user_id, kind, since_iso)
    ).fetchone()[0])


def get_user(db: Database, user_id: int) -> Any | None:
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# Candidate pipeline (SIS 10-11 change request CR-01) ---------------------------------------

CANDIDATE_STATUSES = ("new", "shortlist", "interview", "offer", "rejected")
MAX_NOTE = 500


def set_candidate_status(db: Database, vacancy_id: int, resume_id: int, status: str, note: str,
                         user_id: int | None) -> None:
    """Status of one candidate for one vacancy (a person can be shortlisted for one vacancy
    and rejected for another). "new" with an empty note removes the row: that is the default."""
    if status not in CANDIDATE_STATUSES:
        raise ValueError(status)
    note = note.strip()[:MAX_NOTE]
    with db:
        if status == "new" and not note:
            db.execute("DELETE FROM candidate_status WHERE vacancy_id = ? AND resume_id = ?", (vacancy_id, resume_id))
        else:
            db.execute(
                """INSERT INTO candidate_status (vacancy_id, resume_id, status, note, updated_at, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (vacancy_id, resume_id) DO UPDATE SET
                     status = excluded.status, note = excluded.note,
                     updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
                (vacancy_id, resume_id, status, note, now(), user_id),
            )


def is_scored(db: Database, vacancy_id: int, resume_id: int) -> bool:
    return db.execute("SELECT 1 FROM scores WHERE vacancy_id = ? AND resume_id = ?", (vacancy_id, resume_id)).fetchone() is not None


def email_taken(db: Database, email: str) -> bool:
    """SIS 10-11: one account per email (case-insensitive), so usage stats count real people."""
    return db.execute("SELECT 1 FROM users WHERE lower(email) = lower(?) LIMIT 1", (email,)).fetchone() is not None


def get_user_by_name(db: Database, username: str) -> Any | None:
    # Usernames are unique ignoring case (SQLite: COLLATE NOCASE, PostgreSQL: unique index on lower()).
    return db.execute("SELECT * FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()


def list_users(db: Database) -> list[Any]:
    return db.execute(
        """
        SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.owner_id = u.id) AS session_count
        FROM users u ORDER BY lower(u.username)
        """
    ).fetchall()


def update_user(db: Database, user_id: int, **fields: Any) -> None:
    allowed = {"password_hash", "role", "active", "last_login_at", "totp_secret", "totp_last_step", "recovery_codes"}
    items = [(k, seal("users", k, v) if k == "totp_secret" else v) for k, v in fields.items() if k in allowed]
    if not items:
        return
    with db:
        # Column names come from the `allowed` set above, values are bound.
        db.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k, _ in items)} WHERE id = ?",  # noqa: S608
                   (*[v for _, v in items], user_id))


# SEC-01: password reset links ------------------------------------------------------------------

def create_password_reset(db: Database, token_hash: str, user_id: int, created_by: int | None,
                          expires_at: str) -> None:
    with db:
        # One valid link per user: a new link replaces older unused ones.
        db.execute("DELETE FROM password_resets WHERE user_id = ? AND used_at IS NULL", (user_id,))
        db.execute(
            "INSERT INTO password_resets (token_hash, user_id, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token_hash, user_id, created_by, now(), expires_at),
        )


def get_password_reset(db: Database, token_hash: str) -> Any | None:
    """An unused, unexpired reset link."""
    return db.execute(
        "SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
        (token_hash, now()),
    ).fetchone()


def use_password_reset(db: Database, token_hash: str, password_hash: str) -> bool:
    """Set the new password and burn the link in one transaction; False if it was already used."""
    with db:
        row = db.execute(
            "UPDATE password_resets SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND expires_at > ? "
            "RETURNING user_id",
            (now(), token_hash, now()),
        ).fetchone()
        if row is None:
            return False
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, row["user_id"]))
    return True


def audit(db: Database, action: str, user: Any | None = None, session_id: int | None = None,
          detail: str | None = None, username: str | None = None) -> None:
    """SEC-03: one row in the append-only audit_log and the same event as a structured (JSON)
    log line on the `hrscreen.audit` logger, so it also reaches the central logs (OBS-02)."""
    event = {
        "created_at": now(), "user_id": user["id"] if user else None,
        "username": user["username"] if user else username, "action": action,
        "session_id": session_id, "detail": (detail or "")[:500] or None,
    }
    with db:
        event_id = _rowid(db.execute(
            "INSERT INTO audit_log (created_at, user_id, username, action, session_id, detail) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (event["created_at"], event["user_id"], event["username"], action, session_id, event["detail"]),
        ).fetchone())
    audit_logger.info("audit %s", json.dumps({"event": "audit", "id": event_id, **event}, ensure_ascii=False))


def audit_once(db: Database, action: str, user: Any | None, session_id: int | None,
               detail: str | None, minutes: int = 10) -> bool:
    """SIS 10-11 fix: log a view only if the same user has not logged the same view in the
    last `minutes`; refreshes and filter clicks no longer flood the audit log."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    seen = db.execute(
        "SELECT 1 FROM audit_log WHERE action = ? AND user_id IS NOT DISTINCT FROM ? AND session_id IS NOT DISTINCT FROM ? "
        "AND detail IS NOT DISTINCT FROM ? AND created_at >= ? LIMIT 1",
        (action, user["id"] if user else None, session_id, detail, since),
    ).fetchone()
    if seen is None:
        audit(db, action, user, session_id, detail)
    return seen is None


def list_audit(db: Database, limit: int = 200) -> list[Any]:
    return db.execute("SELECT * FROM audit_log ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()


def search_audit(db: Database, *, action: str | None = None, username: str | None = None,
                 since: str | None = None, until: str | None = None, offset: int = 0,
                 limit: int = 50) -> list[Any]:
    """SEC-03: one page of the audit log, newest first. `since`/`until` are ISO UTC bounds
    (`since` included, `until` excluded); `username` matches whole names, any letter case."""
    where, params = [], []
    if action:
        where.append("action = ?")
        params.append(action)
    if username:
        where.append("LOWER(username) = LOWER(?)")
        params.append(username)
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if until:
        where.append("created_at < ?")
        params.append(until)
    # Only the fixed fragments above go into the text; every value is a parameter.
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "")  # noqa: S608
    return db.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
