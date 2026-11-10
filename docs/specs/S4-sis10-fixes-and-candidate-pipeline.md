# S4 — SIS 10-11: quality fixes and the candidate pipeline (change request CR-01)

## Fixes found by testing (Part 2)

### BUG-01 — every visitor had the same IP behind Cloudflare Tunnel (critical)
- Symptom: cloudflared runs on the server and connects to `127.0.0.1`, so
  `request.remote_addr` was `127.0.0.1` for everyone. After 5 sign-ups in an hour
  registration was closed for the whole class; 5 wrong passwords from anybody locked
  every visitor out for 15 minutes; the audit log showed one IP.
- Fix: `auth.client_ip()`. If the direct peer is in `TRUSTED_PROXIES` (default
  `127.0.0.1,::1`), the visitor IP is taken from `CF-Connecting-IP`, else from the last
  `X-Forwarded-For` hop. Values must parse as an IP address. From any other peer the
  headers are ignored, so they cannot be forged to dodge the limits.
- Entries may be addresses or networks. `deploy/docker-compose.yml` sets
  `127.0.0.1,::1,172.16.0.0/12`, because there cloudflared reaches the app over the
  Docker network.

### BUG-02 — one email could be used by many accounts (high)
- Sign-up and admin "create user" reject an email that already exists (case-insensitive):
  "Эта почта уже используется". Admin-created users may still have no email.

### BUG-03 — the audit log was flooded by ranking views (medium)
- `ranking_open` is written once per user, vacancy and 10 minutes (`storage.audit_once`),
  so refreshes and filter clicks no longer bury real actions in the teacher's report.

## CR-01 — candidate pipeline (Part 4)
- Each candidate has a status per vacancy: Новый → Шорт-лист → Интервью → Оффер / Отказ
  (`new`, `shortlist`, `interview`, `offer`, `rejected`) and a note up to 500 characters.
  Stored in `candidate_status (vacancy_id, resume_id)`; rows cascade with the vacancy or
  resume. "Новый" without a note is the default and is not stored.
- Ranking page: a status select and a note field under every candidate. With JavaScript
  the status saves as soon as it is changed (no reload); without it the form posts and
  redirects back to the same filter.
- Filter chips with counts per status and a search by name, file or headline
  (`?status=shortlist&q=иванова`). Ranks stay as in the full ranking.
- CSV, Excel and PDF exports include status and note and follow the current filter, so
  "Excel of the shortlist" is one click.
- `POST /sessions/<sid>/vacancies/<vid>/candidates/<rid>/status` — owner-scoped (404),
  CSRF-protected, 400 for an unknown status, 404 if the resume is not scored for that
  vacancy, audited as `candidate_status`.
- No AI call, no new dependency.

## Tests
`tests/test_sis10_fixes.py` (10) and `tests/test_candidate_status.py` (19); 354 in total.
