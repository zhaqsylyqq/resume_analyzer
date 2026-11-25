# APP-02 — PostgreSQL support and schema migrations

## Decision

One code base for both engines, chosen by one setting:

- `DATABASE_URL=postgresql://…` → PostgreSQL (production, Docker, later Kubernetes/CloudNativePG);
- empty → the SQLite file `<DATA_DIR>/app.db` (development, the test suite, the laptop until
  APP-03 moves the data).

No ORM: the app keeps its plain, parameterised SQL. `app/services/db.py` is a thin layer
that makes psycopg 3 behave like sqlite3 for this code: `?` placeholders, rows by name and
position, `with db:` transactions (nested blocks commit once), common `IntegrityError` /
`DatabaseError`, and `db.dialect` for the few differences.

## What changed in the SQL

| Before (SQLite only) | Now (both) |
|---|---|
| `cursor.lastrowid` | `INSERT … RETURNING id` |
| `INSERT OR REPLACE` | `INSERT … ON CONFLICT … DO UPDATE` |
| duplicate resume caught as `sqlite3.IntegrityError` | `ON CONFLICT (session_id, sha256) DO NOTHING RETURNING id` |
| `COLLATE NOCASE` on usernames | `lower(username)` lookups; PostgreSQL has a unique index on `lower(username)` |
| `julianday()` for the average analysis time | computed in Python |
| `json_extract()` in the diversity report | parsed in Python |
| `SUM(action = 'login')` | `SUM(CASE WHEN … THEN 1 ELSE 0 END)` |
| `date(col, '+5 hours')` | `db.sql_local_day()` per dialect |
| `IS ?` | `IS NOT DISTINCT FROM ?` |
| `BEGIN IMMEDIATE` to claim resumes | PostgreSQL: `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED) RETURNING id` — the queue pattern APP-04 builds on |

Timestamps stay ISO-8601 text in UTC on both engines, so comparisons and the data move are
identical.

## Schema migrations

- PostgreSQL: Alembic, raw-SQL revisions in `app/migrations/versions/` (`0001_initial` = the
  current schema including SEC-01). Applied at start-up under a PostgreSQL advisory lock (safe
  with several replicas) and by `flask --app app db-upgrade`. New revision:
  `alembic revision -m "…"` (uses `DATABASE_URL`).
- SQLite: unchanged built-in schema with additive `_migrate()`.
- `flask --app app db-check` shows the engine, the masked URL and the schema revision.

## Deployment pieces

- `deploy/docker-compose.yml`: a `db` service (postgres:16 pinned by digest, volume `db-data`,
  health check, no published port); the app waits for it and gets `DATABASE_URL`.
  Needs `POSTGRES_PASSWORD` in `.env`.
- New dependencies: `psycopg[binary]` 3.3, `alembic` 1.20 (with SQLAlchemy 2.1 for Alembic only).
- File backups (`backup`, `backup-offsite`, `backup-auto`) remain SQLite-only and say so on
  PostgreSQL; encrypted `pg_dump` copies come with APP-03, before the site switches over.

## Verification

- The whole suite runs on both engines: SQLite 501 passed; PostgreSQL 16 — 481 passed,
  23 skipped (SQLite file backups). CI got a `Tests on PostgreSQL` job with a postgres:16
  service container.
- `tests/test_database.py`: placeholder translation, rows, masked URL, `DATABASE_URL`
  validation, nested transactions, case-insensitive unique usernames, idempotent migrations,
  four concurrent workers claiming 40 resumes with no duplicates (SKIP LOCKED), `db-upgrade`.
- Docker: the image with a postgres:16 container — `/readyz` ready, schema `0001_initial`,
  `create-admin`, `db-check`, clean `docker stop`.
