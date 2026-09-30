# Spec F3 + F6 — Session storage, history and append-only analysis

## Goal
Every screening is a saved session that can be reopened later, extended with new resumes
or vacancies, and never pays twice for work already done.

## Storage (`app/services/storage.py`, SQLite via `sqlite3`)
| Table | Purpose |
|-------|---------|
| `sessions` | title, report language, created/updated timestamps (UTC) |
| `vacancies` | per-session vacancy title/text, cached bias report (B1) |
| `resumes` | display name, SHA-256 (unique per session), extracted text, status, error code, profile JSON **without demographics** |
| `demographics` | resume → gender, age only; used solely for B2 aggregates |
| `scores` | (resume, vacancy) → score, verdict, summary, details JSON |

- WAL mode, foreign keys with `ON DELETE CASCADE`, parameterized SQL only.
- Original files are not stored — only extracted text (data minimization).
- On startup `processing` rows go back to `pending` (crash/restart recovery).

## History (F3)
- `GET /sessions` lists sessions (newest activity first) with vacancy/resume counts and
  analysis progress; the home page shows the six most recent.
- `GET /sessions/<id>` reopens a session with all stored resumes, statuses and vacancies.
- `POST /sessions/<id>/delete` removes a session and everything under it (confirm dialog).

## Append-only analysis (F6)
- New uploads to an existing session are de-duplicated by SHA-256; only new resumes are
  extracted and scored. Existing scores are never rewritten.
- `POST /sessions/<id>/vacancies` adds a vacancy (max 10). Analyzed resumes are re-queued
  only for the missing (resume, vacancy) pairs: no re-extraction, one scoring call per
  resume covering just the new vacancy.
- A stalled queue (e.g. after restart) shows "Continue analysis"; failed resumes show
  "Retry failed". Both call `POST /sessions/<id>/analyze`.

## Acceptance
Tests: append analyzes only new files and leaves old scores untouched; adding a vacancy
makes zero extraction calls and scores each resume only for the new vacancy; restart
recovery; history counts; cascade delete; exclusive queue claiming.
