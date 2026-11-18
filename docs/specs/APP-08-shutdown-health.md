# APP-08 — Health checks and graceful shutdown

## What changed

- **`/healthz` — liveness.** "The process answers." It never touches the database, so a slow
  disk cannot get a working process restarted. Unchanged.
- **`/readyz` — readiness (new).** `200 {"status":"ready"}` when the database answers and the
  server is not stopping; `503 {"status":"shutting_down"}` or `503 {"status":"database_unavailable"}`
  otherwise. A load balancer or Kubernetes sends traffic only to ready copies. Public, like
  `/healthz`, and reveals nothing else.
- **Graceful shutdown.** On SIGTERM (`docker stop`, Kubernetes) or Ctrl+C the server:
  1. switches `/readyz` to 503 and stops taking new resumes;
  2. waits for the resumes already being analyzed, at most `SHUTDOWN_TIMEOUT` seconds
     (default 25, setting checked by APP-01);
  3. exits with code 0 and logs `Stopped cleanly`.

  Resumes not started yet stay `pending` in the database and continue after the next start
  (QW-05). A resume cut off by a hard kill is moved back to `pending` on start, so nothing is
  lost either way; graceful shutdown only avoids paying the AI twice for the same resume.
- Docker: `STOPSIGNAL SIGTERM`; compose `stop_grace_period: 30s`; CI's image smoke test now
  calls `/readyz`, runs `docker stop -t 30` and requires exit code 0 and `Stopped cleanly`.

## Decision: gunicorn later, with APP-04

The roadmap item mentions gunicorn. The analysis queue still lives inside the web process
(threads), so several gunicorn worker processes would each run their own queue and could
analyse the same session twice. Gunicorn also does not run on Windows, where the public site
lives today. It is introduced together with APP-04, when analysis moves into its own worker
process; until then waitress (one process, 8 threads) stays.

## Known limit on the laptop

`scripts/serve_public.ps1` stops the server with `Stop-Process -Force` when the window is
closed, which is a hard kill (Windows has no SIGTERM for another process). Nothing is lost —
interrupted resumes go back to `pending` and resume on the next start — but one AI request
may be repeated. Ctrl+C in the server's own console window is graceful.

## Verification

- `tests/test_shutdown.py` (9 tests): `/readyz` ready / shutting down / database down, uploads
  during shutdown stay pending and run after restart, drain waits for the resume in flight and
  gives up after the timeout, the setting, signal handlers (and skipped outside the main thread).
- Real waitress: `kill -TERM` → `Received SIGTERM… Stopped cleanly`, exit code 0.
