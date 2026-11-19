# APP-04 — Analysis in separate worker processes; gunicorn

## Why

The analysis queue lived in threads of the web process: a web restart interrupted it, and a
second copy of the site would have run a second queue. Kubernetes needs stateless web pods
and workers that scale on their own.

## How

- `WORKER_MODE=inline` (default, the laptop): unchanged — threads inside waitress.
- `WORKER_MODE=external`: the web process only saves resumes as `pending` (the database is
  the queue). `flask --app app worker [--threads N]` processes analyze them:
  - per thread: next session with pending resumes, round-robin (Q1 fairness kept) → claim one
    resume (`FOR UPDATE SKIP LOCKED` on PostgreSQL — workers never take the same one) →
    analyze with the same code as inline mode → repeat; idle → wait `WORKER_POLL_SECONDS`;
  - every minute: resumes `processing` for longer than `STALE_PROCESSING_MINUTES` (their worker
    died) go back to `pending`; a web restart no longer resets `processing` in this mode;
  - SIGTERM/Ctrl+C: stop taking work, finish resumes in hand (`SHUTDOWN_TIMEOUT`), exit 0;
  - one log line per resume with token counts (no content).
- "Analysis running" and the ETA come from the database in external mode, so every web
  process shows the same state.
- gunicorn (Linux only, `sys_platform != "win32"`): `gunicorn -c python:app.gunicorn_conf
  "app:create_app()"` — gthread, 2 processes × 4 threads (`WEB_PROCESSES`, `WEB_THREADS`),
  graceful timeout = `SHUTDOWN_TIMEOUT`, no access log, control socket off (read-only home).
  Use it only with `WORKER_MODE=external`.
- `deploy/docker-compose.yml`: `app` runs gunicorn with `WORKER_MODE=external`; new `worker`
  service (`docker compose up -d --scale worker=2`).

## Verification

- `tests/test_worker.py` (9): web only queues; worker analyzes and stops when empty; sessions
  take turns; run/stop in threads; stale requeue only touches old ones; a web restart keeps
  workers' resumes; worker refuses to start without the AI key; settings; gunicorn config.
- Concurrency on PostgreSQL: 4 claimers, 40 resumes, no duplicates (APP-02 test).
- Both engines: SQLite 510 passed, PostgreSQL 498 passed.
- Docker: postgres:16 + web on gunicorn (`/readyz` ready) + two worker containers; `docker stop`
  of web and worker → exit 0, "Worker stopped cleanly".
