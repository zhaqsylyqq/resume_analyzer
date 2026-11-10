"""F3 — SQLite storage for screening sessions, vacancies, resumes and scores.

All SQL uses parameters. Demographics live in their own table with only what B2 needs
(gender, age) and are never joined into ranking or export queries. Users, session owners
and the audit log belong to A1 (login and roles).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from flask import Flask, current_app, g

from .schemas import CandidateProfile, FitAssessment

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
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_vacancies_session ON vacancies(session_id);
CREATE INDEX IF NOT EXISTS idx_resumes_session_status ON resumes(session_id, status);
CREATE INDEX IF NOT EXISTS idx_scores_vacancy ON scores(vacancy_id, score DESC);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=30, detect_types=0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 30000")
    return db


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app: Flask) -> None:
    db = connect(app.config["DATABASE"])
    try:
        db.executescript(SCHEMA)
        _migrate(db)
        # Work interrupted by a restart goes back to the queue.
        db.execute("UPDATE resumes SET status = ? WHERE status = ?", (PENDING, PROCESSING))
        db.commit()
    finally:
        db.close()
    app.teardown_appcontext(close_db)


def _migrate(db: sqlite3.Connection) -> None:
    """Additive migrations for databases created by earlier versions."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
    user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
    if "email" not in user_columns:  # S2
        db.execute("ALTER TABLE users ADD COLUMN email TEXT")
    resume_columns = {row["name"] for row in db.execute("PRAGMA table_info(resumes)")}
    if "started_at" not in resume_columns:  # Q1: time per resume for the ETA
        db.execute("ALTER TABLE resumes ADD COLUMN started_at TEXT")
    if "owner_id" not in columns:
        # Sessions created before login existed have no owner: only admins see them.
        db.execute("ALTER TABLE sessions ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
    db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id, updated_at DESC)")


# Sessions ---------------------------------------------------------------------------------

def create_session(
    db: sqlite3.Connection, title: str, language: str, vacancies: Iterable[tuple[str, str]],
    owner_id: int | None = None,
) -> int:
    stamp = now()
    with db:
        session_id = db.execute(
            "INSERT INTO sessions (title, language, created_at, updated_at, owner_id) VALUES (?, ?, ?, ?, ?)",
            (title, language, stamp, stamp, owner_id),
        ).lastrowid
        for vacancy_title, text in vacancies:
            db.execute(
                "INSERT INTO vacancies (session_id, title, text, created_at) VALUES (?, ?, ?, ?)",
                (session_id, vacancy_title, text, stamp),
            )
    return int(session_id)


def touch_session(db: sqlite3.Connection, session_id: int) -> None:
    db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now(), session_id))


def get_session(db: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


ALL_OWNERS = object()  # sentinel: no owner filter (admins)


def list_sessions(db: sqlite3.Connection, limit: int = 100, owner_id: Any = ALL_OWNERS) -> list[sqlite3.Row]:
    """Newest first. `owner_id` limits the list to one user's sessions; admins pass ALL_OWNERS."""
    where, params = ("", ()) if owner_id is ALL_OWNERS else ("WHERE s.owner_id = ?", (owner_id,))
    return db.execute(
        f"""
        SELECT s.*, u.username AS owner_name,
               (SELECT COUNT(*) FROM vacancies v WHERE v.session_id = s.id) AS vacancy_count,
               (SELECT COUNT(*) FROM resumes r WHERE r.session_id = s.id) AS resume_count,
               (SELECT COUNT(*) FROM resumes r WHERE r.session_id = s.id AND r.status = 'done') AS done_count
        FROM sessions s LEFT JOIN users u ON u.id = s.owner_id
        {where}
        ORDER BY s.updated_at DESC, s.id DESC LIMIT ?
        """,
        (*params, limit),
    ).fetchall()


def sessions_not_updated_since(db: sqlite3.Connection, cutoff_iso: str) -> list[sqlite3.Row]:
    """Sessions whose last change is older than the cutoff (R1 retention)."""
    return db.execute(
        "SELECT id, title, updated_at FROM sessions WHERE updated_at < ? ORDER BY updated_at", (cutoff_iso,)
    ).fetchall()


def delete_session(db: sqlite3.Connection, session_id: int) -> bool:
    with db:
        return db.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0


# Vacancies --------------------------------------------------------------------------------

def add_vacancy(db: sqlite3.Connection, session_id: int, title: str, text: str) -> int:
    with db:
        vacancy_id = db.execute(
            "INSERT INTO vacancies (session_id, title, text, created_at) VALUES (?, ?, ?, ?)",
            (session_id, title, text, now()),
        ).lastrowid
        touch_session(db, session_id)
    return int(vacancy_id)


def list_vacancies(db: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM vacancies WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()


def get_vacancy(db: sqlite3.Connection, session_id: int, vacancy_id: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM vacancies WHERE id = ? AND session_id = ?", (vacancy_id, session_id)
    ).fetchone()


def save_bias_report(db: sqlite3.Connection, vacancy_id: int, report: dict[str, Any]) -> None:
    with db:
        db.execute("UPDATE vacancies SET bias_json = ? WHERE id = ?", (json.dumps(report, ensure_ascii=False), vacancy_id))


# Resumes ----------------------------------------------------------------------------------

def resume_exists(db: sqlite3.Connection, session_id: int, sha256: str) -> bool:
    row = db.execute("SELECT 1 FROM resumes WHERE session_id = ? AND sha256 = ?", (session_id, sha256)).fetchone()
    return row is not None


def add_resume(
    db: sqlite3.Connection, session_id: int, filename: str, sha256: str, text: str, status: str, error: str | None = None
) -> int | None:
    """Insert a resume; returns None if the same file is already in the session."""
    try:
        with db:
            resume_id = db.execute(
                """INSERT INTO resumes (session_id, filename, sha256, text, status, error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, filename, sha256, text, status, error, now()),
            ).lastrowid
            touch_session(db, session_id)
    except sqlite3.IntegrityError:
        return None
    return int(resume_id)


def list_resumes(db: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id, filename, status, error, created_at, analyzed_at, profile_json FROM resumes "
        "WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()


def get_resume(db: sqlite3.Connection, resume_id: int) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()


def claim_pending(db: sqlite3.Connection, session_id: int, limit: int = 1000) -> list[int]:
    """Atomically move pending resumes to processing and return their ids.

    BEGIN IMMEDIATE takes the write lock before reading, so two workers claiming at the
    same time (Q1 worker pool) can never both get the same resume.
    """
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


def average_analysis_seconds(db: sqlite3.Connection, sample: int = 20) -> float | None:
    """Mean analysis time of the last `sample` finished resumes in any session (Q1)."""
    row = db.execute(
        """
        SELECT AVG(seconds) AS avg FROM (
            SELECT (julianday(analyzed_at) - julianday(started_at)) * 86400.0 AS seconds
            FROM resumes WHERE status = ? AND started_at IS NOT NULL AND analyzed_at IS NOT NULL
            ORDER BY analyzed_at DESC LIMIT ?)
        """,
        (DONE, sample),
    ).fetchone()
    return row["avg"] if row and row["avg"] is not None else None


def requeue_failed(db: sqlite3.Connection, session_id: int) -> int:
    with db:
        return db.execute(
            "UPDATE resumes SET status = ?, error = NULL WHERE session_id = ? AND status = ?",
            (PENDING, session_id, ANALYSIS_FAILED),
        ).rowcount


def requeue_unscored(db: sqlite3.Connection, session_id: int) -> int:
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


def set_resume_status(db: sqlite3.Connection, resume_id: int, status: str, error: str | None = None) -> None:
    with db:
        db.execute(
            "UPDATE resumes SET status = ?, error = ?, analyzed_at = CASE WHEN ? THEN ? ELSE analyzed_at END WHERE id = ?",
            (status, error, status in (DONE, ANALYSIS_FAILED), now(), resume_id),
        )


def save_profile(db: sqlite3.Connection, resume_id: int, profile: CandidateProfile) -> None:
    public = profile.model_dump(exclude={"demographics"})
    with db:
        db.execute(
            "UPDATE resumes SET profile_json = ? WHERE id = ?", (json.dumps(public, ensure_ascii=False), resume_id)
        )
        db.execute(
            "INSERT OR REPLACE INTO demographics (resume_id, gender, age) VALUES (?, ?, ?)",
            (resume_id, profile.demographics.gender, _age(profile)),
        )


def _age(profile: CandidateProfile) -> int | None:
    demo = profile.demographics
    if demo.age:
        return demo.age
    if demo.birth_year:
        return datetime.now(timezone.utc).year - demo.birth_year
    return None


def load_profile(row: sqlite3.Row) -> CandidateProfile | None:
    raw = row["profile_json"]
    return CandidateProfile.model_validate_json(raw) if raw else None


def unscored_vacancies(db: sqlite3.Connection, resume_id: int) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT v.* FROM vacancies v JOIN resumes r ON r.session_id = v.session_id
        WHERE r.id = ? AND NOT EXISTS (SELECT 1 FROM scores s WHERE s.resume_id = r.id AND s.vacancy_id = v.id)
        ORDER BY v.id
        """,
        (resume_id,),
    ).fetchall()


def save_scores(db: sqlite3.Connection, resume_id: int, assessments: Iterable[FitAssessment]) -> None:
    stamp = now()
    with db:
        db.executemany(
            """INSERT OR REPLACE INTO scores (resume_id, vacancy_id, score, verdict, summary, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    resume_id,
                    a.vacancy_id,
                    a.score,
                    a.verdict,
                    a.summary,
                    json.dumps(a.model_dump(exclude={"vacancy_id", "score", "verdict", "summary"}), ensure_ascii=False),
                    stamp,
                )
                for a in assessments
            ],
        )


def progress(db: sqlite3.Connection, session_id: int) -> dict[str, int]:
    counts = {PENDING: 0, PROCESSING: 0, DONE: 0, PARSE_FAILED: 0, ANALYSIS_FAILED: 0}
    for row in db.execute(
        "SELECT status, COUNT(*) AS n FROM resumes WHERE session_id = ? GROUP BY status", (session_id,)
    ):
        counts[row["status"]] = row["n"]
    counts["total"] = sum(counts.values())
    return counts


# Users and audit log (A1) -------------------------------------------------------------------

def create_user(db: sqlite3.Connection, username: str, password_hash: str, role: str,
                email: str | None = None) -> int:
    with db:
        return int(db.execute(
            "INSERT INTO users (username, password_hash, role, created_at, email) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, role, now(), email or None),
        ).lastrowid)


def get_settings(db: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}


def save_settings(db: sqlite3.Connection, values: dict[str, str]) -> None:
    stamp = now()
    with db:
        db.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [(k, v, stamp) for k, v in values.items()],
        )


def resumes_added_since(db: sqlite3.Connection, owner_id: int, since_iso: str) -> int:
    """S2: resumes accepted for analysis (not parse failures) in the owner's sessions since a time."""
    return int(db.execute(
        """
        SELECT COUNT(*) FROM resumes r JOIN sessions s ON s.id = r.session_id
        WHERE s.owner_id = ? AND r.created_at >= ? AND r.status != ?
        """,
        (owner_id, since_iso, PARSE_FAILED),
    ).fetchone()[0])


def get_user(db: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_name(db: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def list_users(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.owner_id = u.id) AS session_count
        FROM users u ORDER BY u.username COLLATE NOCASE
        """
    ).fetchall()


def update_user(db: sqlite3.Connection, user_id: int, **fields: Any) -> None:
    allowed = {"password_hash", "role", "active", "last_login_at"}
    items = [(k, v) for k, v in fields.items() if k in allowed]
    if not items:
        return
    with db:
        db.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k, _ in items)} WHERE id = ?",
                   (*[v for _, v in items], user_id))


def audit(db: sqlite3.Connection, action: str, user: sqlite3.Row | None = None, session_id: int | None = None,
          detail: str | None = None, username: str | None = None) -> None:
    with db:
        db.execute(
            "INSERT INTO audit_log (created_at, user_id, username, action, session_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (now(), user["id"] if user else None, user["username"] if user else username, action, session_id,
             (detail or "")[:500] or None),
        )


def list_audit(db: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM audit_log ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
