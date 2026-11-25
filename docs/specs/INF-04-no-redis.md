# INF-04 — Redis: closed, not needed

The plan (2026-11-12) gave Redis one job: **state shared by several copies** of the web app
and the workers. Every piece of that state already has a home:

| Shared state | Where it lives | Since |
|---|---|---|
| Rate limits and sign-in lock-outs | `rate_counters` / `rate_locks` in the app database | APP-06/07 |
| Analysis queue | `resumes.status` with `FOR UPDATE SKIP LOCKED` in PostgreSQL | APP-04 |
| Signed-in sessions | signed cookie (Flask), nothing on the server; a password change or 2FA reset signs every device out | QW / SEC-01 |
| Daily AI limits | `llm_usage` in the app database | S2 / QW-02 |
| Metrics of several processes | `PROMETHEUS_MULTIPROC_DIR` (per pod) | OBS-01 |

Load is far below what makes a database counter a problem (one indexed upsert per limited
request; the site serves tens of users). So Redis would add a service to run, patch, secure,
back up and watch, with nothing to do.

## What this changes in the plan

- No `deploy/envs/*/redis.yaml`; INF-05 (Helm chart) no longer depends on INF-04.
- The INF-04 risk "a Redis restart signs users out" disappears.
- `services/ratelimit.py` keeps a `Store` interface: if the site ever outgrows the database
  counters, a Redis store can be added there without touching callers. Signal to revisit:
  `hrscreen_http_request_duration_seconds` of limited endpoints grows with the number of web
  pods, or the database CPU is dominated by `rate_counters`.

Decided with the owner's rule "no extra services unless needed" (2026-11-17) and recorded at
APP-06/07 (2026-11-19).
