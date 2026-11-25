# APP-05 — File parsing in the worker

## Why

Extracting text from 50 PDF/DOCX files (up to 5 MB each) ran inside the upload request: a big
batch held a web thread for seconds and could hit request timeouts behind gunicorn/Cloudflare.

## How (WORKER_MODE=external only; inline mode is unchanged)

- The upload request does only the cheap checks (`parsing.precheck`: file type, size, empty)
  and the duplicate check by SHA-256, then stores the file in `resumes.raw_file`
  (BLOB / BYTEA) with status `pending` and empty text. The user gets the answer at once.
- The worker, before analysing a resume that still has `raw_file`, extracts the text with the
  same `parse_file` as before. Success → text saved, analysis continues. Failure (corrupt PDF,
  no text layer…) → `parse_failed` with the same error codes as before, **no AI call**.
- The uploaded document is deleted (`raw_file = NULL`) as soon as the text exists or parsing
  failed: the original file (photo, contacts) is never kept longer than needed.
- No new status: the UI shows such resumes as "waiting" (`pending`), and parse failures appear
  among the failed ones once the worker reaches them. The daily resume limit counts accepted
  uploads; a later parse failure drops out of the count by itself (it already excluded
  `parse_failed`).
- Schema: SQLite column added by `_migrate`; PostgreSQL migration `0002_resume_raw_file`
  (applied at start-up). `db-move` copies `raw_file` too and compares it (hex) in the SHA-256 check.

## Verification

- `tests/test_deferred_parsing.py` (5): PDF/DOCX/TXT stored without parsing in the web process
  (parse_file made to fail there), worker parses and analyses them, files dropped afterwards;
  unsupported/too large/empty still rejected at once; a broken PDF fails in the worker with no
  AI call; duplicates caught before parsing; inline mode still parses in the web process.
- Both engines: SQLite 515 passed, PostgreSQL 503 passed.
