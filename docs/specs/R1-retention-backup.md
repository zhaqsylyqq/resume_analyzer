# R1 — Data retention and database backups

## Goal
Resumes are personal data: keep them only as long as needed, and never lose the database.

## Retention
- Setting `RETENTION_DAYS` (default `90`; `0` turns retention off). A session whose last
  change (`updated_at`: created, vacancy or resumes added) is older than that is deleted
  with everything in it: vacancies, resumes and their text, profiles, scores and
  demographics (foreign keys cascade).
- Runs:
  - at app start;
  - at most once per 24 hours on a later request, so a server that is never restarted
    still purges;
  - on demand: `flask --app app purge-old` (prints how many sessions were deleted;
    `--days N` overrides the setting for one run).
- A session with a running analysis job is skipped and purged next time.
- Each deleted session writes an audit row `session_purge` (user `system`, detail: title
  and the retention period). The audit log itself is kept.

## Backups
- `python scripts/backup_db.py [--db PATH] [--out DIR] [--keep N]`:
  - copies the database with the SQLite online backup API, so the copy is consistent even
    while the app is writing (WAL mode);
  - file name `app-YYYYMMDD-HHMMSS.db` in `--out` (default `instance/backups/`, which is
    git-ignored);
  - checks the copy with `PRAGMA integrity_check` and exits non-zero if it fails or the
    source is missing;
  - `--keep N` (default 14) deletes the oldest backups so at most N remain.
- README explains how to schedule it (Windows Task Scheduler, cron) and to copy backups to
  another disk.

## Acceptance criteria
- Old sessions are deleted with all child rows; recent ones and running ones stay.
- `RETENTION_DAYS=0` deletes nothing; the CLI reports the count.
- A backup taken while another connection writes opens and passes the integrity check;
  rotation keeps exactly N files; a missing database gives a clear error.
