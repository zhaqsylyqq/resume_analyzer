# Spec F1 — Batch resume upload

## Goal
HR drops dozens of resumes at once into a screening session and watches them get
analyzed, with a clear list of files that could not be read.

## Flow
1. `POST /sessions` creates a session with a title, report language (RU/KK) and 1–10
   vacancies (title ≤ 200 chars, text 30–20 000 chars). Validation errors re-render the
   form with messages and the entered values.
2. `POST /sessions/<id>/resumes` (multipart `files`, CSRF header) accepts up to
   `MAX_FILES_PER_BATCH` (50) files of ≤ 5 MB each (request cap 60 MB → 413).
   Each file is parsed (F4); readable files are stored as `pending`, unreadable ones as
   `parse_failed` with an error code; byte-identical files are rejected as `duplicate`.
   Response: `{accepted: [...], rejected: [{filename, error, message}], started}`.
3. A background job analyzes pending resumes (F7) with limited concurrency. Failures are
   stored per resume (`analysis_failed` + reason) and never stop the batch.
4. `GET /sessions/<id>/status` returns counts and per-resume status; the page polls it
   every 2 s while work is in progress.
5. `POST /sessions/<id>/analyze` re-queues failed resumes (and missing scores) and restarts
   the job.

## UI
Drag-and-drop zone (also keyboard/click accessible), client-side pre-checks for type,
size and count, upload progress bar, rejected-file list with reasons, live status list,
stat tiles, retry button, toasts. All text RU/KK.

## Acceptance
Test: 20 mixed PDF/DOCX/TXT files plus a scan, a corrupt DOCX, an `.exe` and a duplicate
in one request → 20 accepted and analyzed, 4 rejected with reasons, no crash.
