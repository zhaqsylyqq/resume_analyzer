# APP-06/07 — Shared rate limits

## Why

The login and sign-up limits lived in the memory of one process. With several gunicorn
processes or containers (APP-04) "5 wrong passwords" became "5 per process", and a restart
forgot them. The expensive actions (uploads, analysis, AI checks of a vacancy, exports) had
no per-user limit at all: a script or a stuck browser tab could spend the AI balance.

## Decision: no Redis

Counters live in the application database (SQLite or PostgreSQL): two small tables,
`rate_counters(key, window_start, count)` and `rate_locks(key, until)`. At our traffic (one
indexed upsert per limited request) the database handles this easily, and there is no new
service to run, back up or secure. `services/ratelimit.py` hides the storage behind a small
`Store` interface, so a Redis store can be added later without touching callers.

Counting is an approximate sliding window: the current fixed window plus the previous one,
weighted by how much of it still overlaps the last `window` seconds. Rows older than two days
and expired locks are deleted now and then (1 % of increments).

`RATE_LIMIT_STORE=memory` keeps the old per-process behaviour (single-process setups, tests).

## Limits

| What | Limit | Counted per |
|---|---|---|
| Wrong password at login (existing) | 5 in 15 min → 15 min lock | user name and address |
| Sign-up (existing) | as before | address |
| Upload resumes, start analysis, create session | 20 a minute | user |
| AI bias check (vacancy page, new-session form) | 10 a minute | user |
| Exports (CSV, Excel, PDF) | 30 a minute | user |
| Setting a new password by reset link (POST only) | 10 in 15 min | address |
| Change password, 2FA confirm/disable, new recovery codes | 10 in 15 min | user |

Over a limit the request gets **429** with a `Retry-After` header: the normal error page
("Слишком много запросов подряд…", RU/KK) or a JSON error for `fetch()` calls. The error
handler now keeps the exception's own headers (Retry-After, Allow).

## Verification

- `tests/test_rate_limits.py` (12): sliding window and lock-out on both stores; two separate
  database stores see one counter (= two processes); cleanup of old rows; failed logins land
  in the database; AI check 429 with Retry-After and JSON message, other users unaffected;
  reset link limited on POST only; account secrets limited; limits can be switched off.
- Both engines: SQLite 527 passed, PostgreSQL 515 passed; combined coverage 94 %
  (rate-limit modules 100 %).
- Schema: SQLite tables created at start-up; PostgreSQL migration `0003_rate_limits`.
