# Progress

## Status
| Phase | Scope | State |
|-------|-------|-------|
| 0 | Skeleton | done |
| 1 | F7 Gemini service | done |
| 2 | F4 parsing + F1 batch upload | done |
| 3 | F3 storage + F6 append | done |
| 4 | F2 ranking UI + CSV | done |
| 5 | B1 bias check + B2 diversity | done |
| 6 | Polish, README, Dockerfile | done |
| 7 | Sprint 2 (SIS 8–9): US7 Excel/PDF export, language diversity, API savings | done |
| 8 | F7b DeepSeek provider + prepaid-balance (402) handling | done, verified live |
| 9 | F7c token usage per batch + DeepSeek in `.env.example` (AGENTS task 1) | done |
| 10 | Review of the export commit `f5c7074` (AGENTS task 2) | done, verified live |
| 11 | A1 login, roles, audit log (AGENTS task 3) | done |
| 12 | R1 data retention + database backups (AGENTS task 4) | done |
| 13 | Q1 fair round-robin queue + ETA (AGENTS task 5) | done |
| 14 | Docker check (AGENTS task 6) | done |
| 15 | S1 usage statistics + sign-up with invite code | done |
| 16 | S2 registration switches, email, daily resume limit | done |
| 17 | Public access: hrscreen.date via Cloudflare Tunnel | live from the owner's laptop; server move pending ([DEPLOY](docs/DEPLOY.md)) |
| 18 | S3 example session for new users + delete a vacancy | done |
| 20 | S5 user feedback: “Подходят” tile filter, mobile layout, score colours | done |
| 19 | S4 (SIS 10-11): real client IP behind the tunnel, unique email, audit de-dup; CR-01 candidate pipeline | done |
| 41 | CD-01: `build.yml` publishes the image to GHCR from `main` and `v*` tags only, by digest; cosign keyless signature, GitHub build-provenance (SLSA Build L2) and SBOM attestations; self-check and refusal of a foreign image; `scripts/verify_image.sh` ([spec](docs/specs/CD-01-registry-signing.md)) | done (publishes after PR №2 is merged) |
| 40 | OBS-01: `/metrics` (HTTP, queue, AI requests and tokens, resumes finished) for the monitoring system only; request ids; JSON logs (`LOG_FORMAT=json`) and scrubbed text logs; optional Sentry without personal data ([spec](docs/specs/OBS-01-observability.md)) | done |
| 39 | SEC-04: threat model reviewed against the code ([threat-model](docs/security/threat-model.md)); 10 hostile resumes in `samples/adversarial/` and `tests/test_prompt_injection.py`; `fence` defuses every spelling of the delimiter tags, role tags and chat tokens; answers that repeat the system prompt are rejected; resumes that talk to the AI are flagged in the ranking and audited (`services/injection.py`); `Cache-Control: no-store`, CORP and COEP; weekly OWASP ZAP baseline of a throw-away copy in CI (`dast.yml`, report-only); `smoke_test.py --adversarial` for the real model once per release ([spec](docs/specs/SEC-04-threat-model-dast.md)) | done; first DAST run: no finding above informational |
| 38 | APP-10: `app/auth.py` split into the `app/auth/` package; `app/policy.py` — one table of access rules (PUBLIC / SIGNED_IN / OWNER / ADMIN) checked before every request, a route without a rule is closed; role × route test matrix; `gemini.py` → `llm.py`, `LLMService`, `LLM_CONCURRENCY` with the old names kept ([spec](docs/specs/APP-10-modules-policy.md)) | done |
| 37 | Backups and checks start read-only (no migration, no encryption pass, no purge, no key file): after the 2026-11-24 incident in which the scheduled `backup-auto` migrated and encrypted the live database ([spec](docs/specs/OPS-readonly-commands.md)) | done |
| 36 | SEC-02b: personal data encrypted in the database (AES-256-GCM per column: resume text, file name, profile, waiting upload, demographics, 2FA secret), `DATA_KEY` / `<data>/.data_key`, encryption of old rows and key rotation at start-up, `python -m app.data_key`, `data-key-rotate` ([spec](docs/specs/SEC-02b-field-encryption.md)) | done; laptop switch pending |
| 35 | SEC-03: append-only audit log (triggers on both engines, `db-app-role` for a least-privilege app role), each event also a JSON log line, new events (access denied, 2FA and password failures, recovery code, reset links), admin page with pages and filters ([spec](docs/specs/SEC-03-audit.md)) | done; laptop role switch pending |
| 34 | CI fix: "Tests on PostgreSQL" was red since APP-02 — `ubuntu-latest` (26.04) has only PostgreSQL 18 tools and pg_restore 18 cannot restore into 16. Backups now use the client tools of the server's major version (clear error otherwise); the job runs on `ubuntu-24.04` ([spec](docs/specs/APP-03-data-move.md)) | done |
| 33 | APP-06/07: rate-limit counters in the app database (shared by all processes, no Redis); 429 + Retry-After on uploads, analysis, AI checks, exports, password reset and account secrets ([spec](docs/specs/APP-06-07-rate-limits.md)) | done |
| 32 | APP-05: with external workers the upload only stores the file; the worker extracts the text, drops the file afterwards; migration 0002 ([spec](docs/specs/APP-05-parsing-in-worker.md)) | done |
| 31 | APP-04: `WORKER_MODE=external` + `flask --app app worker` (database queue, SKIP LOCKED, stale requeue, graceful stop); gunicorn for Linux; compose `worker` service ([spec](docs/specs/APP-04-worker.md)) | done |
| 30 | APP-03: `db-move` SQLite → PostgreSQL with per-table count + SHA-256 comparison; PostgreSQL backups (encrypted pg_dump, restore check), combined coverage in CI ([spec](docs/specs/APP-03-data-move.md)) | done |
| 29 | APP-02: PostgreSQL support (`DATABASE_URL`), Alembic migrations, SKIP LOCKED queue claim, suite runs on both engines, compose `db` service ([spec](docs/specs/APP-02-postgresql.md)) | done; data move is APP-03 |
| 28 | Backup without keeping the USB drive in: `backup-auto` every 30 min copies when the drive is connected, Windows reminder after 3 days without a copy ([spec](docs/specs/APP-09-service-commands.md)) | done |
| 27 | SEC-01: two-factor sign-in (TOTP + recovery codes), own password change, one-time password reset links from admins, `ADMIN_MFA_REQUIRED` ([spec](docs/specs/SEC-01-mfa-password-reset.md)) | done |
| 26 | APP-09: service commands `flask --app app backup / backup-offsite / restore-check / db-check / generate-backup-key`; backup logic in `app/services/backup.py`, scripts kept as wrappers ([spec](docs/specs/APP-09-service-commands.md)) | done |
| 25 | APP-08: `/readyz` readiness check, graceful shutdown on SIGTERM / Ctrl+C (finishes resumes in analysis, `SHUTDOWN_TIMEOUT`); gunicorn deferred to APP-04 ([spec](docs/specs/APP-08-shutdown-health.md)) | done |
| 24 | APP-01: one validated configuration — fail fast on bad values, `APP_ENV=production` profile, secrets from files, `DATA_DIR`, `python -m app.check_config` ([spec](docs/specs/APP-01-config.md)) | done |
| 23 | Stage 1 CI (CI-01…CI-04): ruff + mypy + coverage ≥ 90 %, Semgrep / pip-audit / gitleaks / Trivy (report-only until 2026-12-01), two-stage image pinned by digest with scan and SBOM, actions pinned by SHA, Dependabot, CODEOWNERS ([spec](docs/specs/CI-stage1.md)) | done in code; owner steps in the spec |
| 22 | SEC-02a: no personal data in AI requests — local redaction and local extraction of name, contacts, demographics ([spec](docs/specs/SEC-02a-redaction.md)) | done |
| 21 | Enterprise roadmap, stage 0 (QW-01…QW-06): offsite backup, AI check quota, locked dependencies, HTTPS behind the proxy, queue resume, repository hygiene ([spec](docs/specs/QW-stage0.md)) | done in code; owner steps in the spec |

## Done
- Phase 0: app factory, env-based config, CSRF + security headers, RU/KK i18n, design system
  (light/dark, responsive), error pages, pytest harness, `scripts/smoke_test.py`.

- Phase 1: `GeminiService` (extraction + multi-vacancy scoring, pydantic-validated JSON,
  retry once, backoff with retry hints, shared 429 cooldown, 503 fallback model,
  concurrency cap), `anonymize.scoring_view` + RU/KK/EN demographic scrubber, prompt
  injection fencing. Verified live with `scripts/smoke_test.py --pipeline` (RU and KK).

- Phase 2: `parsing.py` (PDF/DOCX/TXT, signature sniffing, zip-bomb/page/size limits,
  encoding fallback), batch upload endpoint with duplicate detection, background job
  runner (`jobs.py`), session create form (multi-vacancy), session workspace with
  drag-and-drop + live status polling, 20 synthetic RU/KK samples + 3 bad files
  (`scripts/make_samples.py`). The SQLite schema (`storage.py`) was built here because
  uploads need a session to attach to.

- Phase 3: add-vacancy flow scoring only missing pairs, "continue analysis" after restart,
  flash messages, shared test helpers, tests for append-only behaviour, recovery, history
  and cascade delete.

- Phase 4: ranking with competition ranks and tie-preserving Top-N, ranking page (tabs,
  stats, score bars, verdicts, expandable details, mobile cards), CSV export (BOM, `;`,
  formula-injection safe, RFC 5987 filename). Live e2e run against real Gemini: 6 samples
  uploaded, scan rejected, sensible ranking, CSV OK. Found and fixed: per-model circuit
  breaker for 429/503 with instant fallback; retry budget now 10 (configurable).
- Phase 5: B1 lexicon (RU/KK/EN, inclusive-phrase suppression) + Gemini bias check with
  verified quotes and neutral rewrite; B2 shortlist-vs-pool metrics with four-fifths rule
  and small-cell suppression. Live AI check found all 7 issues in the biased sample.

- Phase 6: README, Dockerfile (waitress, non-root, health check, instance volume),
  `.dockerignore`, GitHub Actions CI (pytest), WCAG AA contrast fixes (dark text on the
  brand orange), localized JSON errors, quieter httpx logs. Fixed two job-runner races
  (resume queued as a job finishes; vacancy added mid-scoring) with regression tests.
  Verified on waitress: every page, assets under CSP, ranking with B1/B2 panels, CSV,
  Kazakh UI and the bias-check API.

- Fix (after phase 6): users hit "Gemini overloaded" constantly. Root cause was the
  free-tier **daily** quota (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, 20 per
  model), not overload. `GEMINI_FALLBACK_MODEL` is now a comma-separated chain; a daily-
  quota 429 takes the model out for an hour and switches instantly; when every model is
  out, resumes fail fast with a dedicated "daily limit" message. Verified live: fell
  through two exhausted models to `gemini-3.5-flash-lite` in 15 s with good results.

- Phase 7 (Sprint 2, SIS 8–9): US7 export — Excel (`.xlsx`, summary sheet + one sheet per
  vacancy, numbers stay numeric, formula-injection safe) and a PDF shortlist report with the
  bundled DejaVu Sans font (RU/KK letters); whole-session Excel export from the session page;
  Excel/PDF/CSV buttons on the ranking page. B2 now also compares resume language (RU/KK/EN).
  API savings: cheapest flash-lite model in `.env.example`, compact JSON in the scoring
  prompt, resumes cut at 20 000 chars, 5 retries instead of 10. Exports and diversity make
  no AI calls (covered by a test). 14 new tests, 194 in total.

- Phase 8 (F7b, [spec](docs/specs/F7b-deepseek-provider.md)): `LLM_PROVIDER=deepseek` runs
  the engine on DeepSeek (`deepseek-flash`, JSON mode, schema in the system prompt, thinking
  off) via `openai_compat.OpenAICompatBackend`. `GeminiService` now delegates the request
  to a backend, while retries, cooldowns and validation stay shared. HTTP 402 (empty
  prepaid balance) on either provider fails the batch fast with a `billing_exhausted`
  RU/KK message. `smoke_test.py` honours `LLM_PROVIDER`. 14 new tests, 208 in total. Why:
  the Gemini paid tier asked for a $30 first prepayment, while DeepSeek works with ~$5.
  - Verified live on 2026-11-10: ping, `--pipeline` (score 92 in RU and KK, demographics
    kept apart) and `--bias` (7 of 7 issues found, as with Gemini) all pass.
  - Full batch of 20 samples x 2 vacancies (RU Python, KK data analyst): 20/20 done with
    no retries, 125 s (about 6 s per resume), 40 calls.
  - Tokens: 55k prompt (60% cache hit) and 17.5k completion, i.e. about
    $0.0007–0.0014 per resume. $5 covers roughly 3 500–7 000 resumes.
  - Fixed: `smoke_test.py` crashed printing RU/KK text on Windows consoles (cp1252); it
    now writes UTF-8.

- Phase 9 (F7c, [spec](docs/specs/F7c-usage-logging.md)): both backends report the token
  counts every reply carries (DeepSeek `prompt_cache_hit_tokens`, OpenAI `cached_tokens`,
  Gemini `usage_metadata`) into a `UsageCounter` bound per batch with `usage_scope()`; the
  job runner logs one INFO line per finished batch (resumes, requests, prompt / cache-hit /
  completion tokens), never content or keys. `.env.example` now starts with a DeepSeek
  block, Gemini kept as a commented alternative. 5 new tests, 213 in total. No live run was
  needed: there were no `invalid_output` failures to fix in the JSON instruction.

- Phase 10 (review of `f5c7074`, AGENTS task 2): read the diff against the rules (no
  demographics in exports, routes scoped by session, CSP untouched, RU+KK keys, model names
  from config) and exercised the exports with a mocked provider. Found and fixed:
  - Excel export crashed (500) when AI text contained a control character (`\x01`,
    `\x0b` ...), which xlsx forbids; such characters are now removed.
  - Excel showed a leading `'` on text starting with `= + - @` (for example a strength
    written as "- опыт"). Cells are now typed as strings instead: shown as written, never
    evaluated (verified in LibreOffice and by checking the sheet XML has no formulas).
  Checked and fine: Cyrillic/Kazakh in PDF, numbers numeric in Excel, CSV unchanged,
  404 for foreign ids, no AI calls on export. 1 new test, 214 in total.
  Live run on 2026-11-10 (DeepSeek `deepseek-flash`, 5 sample resumes × 2 vacancies, RU
  Python + KK data analyst): 5/5 done; Excel per vacancy and per session open, scores
  numeric, no formula cells, no demographic columns; PDFs show Russian and Kazakh letters;
  CSV has the BOM. Usage line: 10 requests, prompt 13 853 tokens (8 960 cache hit),
  completion 4 532 tokens, i.e. under $0.001 per resume.

- Phase 11 (A1, [spec](docs/specs/A1-auth.md)): login with Argon2id password hashes
  (`argon2-cffi`), roles `recruiter` / `admin`, `sessions.owner_id` (added by an in-place
  migration; old sessions stay ownerless and admin-only), every query and route scoped by
  owner (404 for foreign sessions, including status, upload, analyze, exports, bias
  re-check and delete), admin pages for users and the audit log, `flask create-admin`
  CLI, in-memory rate limit for failed logins, session renewal on login and sign-out on
  password change or deactivation. RU/KK UI. 40 new tests (254 in total); existing tests
  now sign in as a test recruiter. Checked in the browser (RU, KK, phone width).

- Phase 12 (R1, [spec](docs/specs/R1-retention-backup.md)): `RETENTION_DAYS` (default 90,
  0 = off) deletes sessions not changed for that long, with all child rows, at start, once
  a day on a request and via `flask purge-old [--days N]`; running jobs are skipped; each
  deletion is audited as `session_purge`. `scripts/backup_db.py` copies the DB with the
  SQLite backup API, runs `integrity_check`, rotates (`--keep`, default 14) and is in the
  Docker image. README documents scheduling (Task Scheduler, cron, Docker). 11 new tests,
  265 in total.

- Phase 13 (Q1, [spec](docs/specs/Q1-fair-queue-eta.md)): the per-session worker thread is
  replaced by one process-wide pool of `GEMINI_CONCURRENCY` workers and a rotation of
  sessions; each turn claims one resume, so sessions alternate (a session added later gets
  every other turn at once). Batches still end with one usage log line; the race fixes
  (resume queued while a job finishes, vacancy added mid-scoring) keep their tests.
  `resumes.started_at` (migration) feeds the ETA: average of the last 20 analyzed resumes
  × round-robin turns ÷ workers, shown on the session page and in `/status`
  (`eta_seconds`, `eta_label`), RU/KK. 12 new tests, 277 in total. Checked in the
  browser with a slowed-down fake provider.

- Phase 14 (Docker check, AGENTS task 6): built and ran the image on Docker 29; checked
  `/healthz` (container `healthy`), login redirect, `flask create-admin` via `docker exec`,
  a screening of all 20 samples against 2 vacancies through the DeepSeek backend pointed
  at an OpenAI-compatible stand-in server (no real key), Excel/PDF export, audit log,
  `backup_db.py` inside the container, and data after `docker restart` and after
  recreating the container on the same volume. README Docker section rewritten to match.
  The check found a real bug in phase 13: two workers could claim the same resume (20
  resumes cost 50 requests instead of 40). `claim_pending` now takes the write lock first
  (`BEGIN IMMEDIATE`) and only updates rows still pending; a 6-thread test covers it
  (278 tests). Backup folders are created with mode 750.

- Phase 15 (S1, [spec](docs/specs/S1-usage-stats-and-signup.md)): the course grade needs
  proof of 20–30 users. `/admin/stats` shows registered / signed-in / active users,
  sessions, analyzed resumes and exports (all time, 7 or 30 days, days in UTC+5), an SVG
  chart by day, a per-user table and an Excel report (summary, by day, users, audit log).
  `/register` with `INVITE_CODE` lets people create their own recruiter accounts
  (constant-time code check, rate-limited per IP, audited as `register`). 16 new tests,
  294 in total. Checked in the browser.

- Phase 16 (S2, [spec](docs/specs/S2-registration-settings-and-limits.md)): a `settings`
  table holds admin switches edited on the users page (registration open, require invite
  code, the code, daily resume limit; audited, no restart). Sign-up asks for an email
  (`users.email`, migration; also optional for admin-created users, shown in users, stats
  and its Excel). At most 5 sign-ups per IP per hour. Recruiters may add at most N resumes
  per 24 hours (default 30, 0 = none, admins exempt); extra files are rejected with a
  clear message and the upload card shows what is left. 21 new tests, 315 in total.

- Phase 17 (public access): the domain `hrscreen.date` was bought at Cloudflare Registrar
  and the named tunnel `hrscreen` serves it.
  - `scripts/serve_public.ps1` runs waitress on 127.0.0.1 plus `cloudflared tunnel run`
    from the owner's laptop.
  - Verified from the internet on 2026-11-10 (then still behind an interim
    shared-password gate, since replaced by A1 login): pages redirect to `/login`, JSON
    returns 401, `/healthz` returns 200, and the session cookie is
    `Secure; HttpOnly; SameSite=Lax`.
  - Cloudflare Access with email OTP was tried and dropped, because adding every
    person by email was too much overhead.
  - `deploy/docker-compose.yml` (app + cloudflared) and `docs/DEPLOY.md` prepare the move
    to an always-on server (not run yet).

- Phase 18 (S3, [spec](docs/specs/S3-starter-session-and-vacancy-delete.md)): every new
  user gets an example session with the Python (Middle/Senior) vacancy (no AI call, not
  counted as their own session in statistics, `STARTER_SESSION=0` turns it off); vacancies
  can be deleted from the session page (scores cascade, resumes stay; audited). 10 new
  tests, 325 in total.

- Phase 19 (S4 / SIS 10-11, [spec](docs/specs/S4-sis10-fixes-and-candidate-pipeline.md)):
  testing found that behind Cloudflare Tunnel every visitor was 127.0.0.1 (sign-up closed
  for everybody after 5 accounts an hour; one bad guesser locked everyone out) — fixed with
  `TRUSTED_PROXIES` + `CF-Connecting-IP`; one account per email; ranking views logged
  once per 10 minutes. Client change request CR-01: candidate statuses with notes, status
  filter, name search, status/note in CSV/Excel/PDF. 29 new tests, 354 in total.

- Phase 20 (S5, [spec](docs/specs/S5-mobile-and-fit-filter.md)): pilot users asked for a
  clickable "Подходят" number, a usable phone layout and clearer score colours. The tile
  now filters to score >= 65 (`fit=1`, also in exports); the top bar, session page and admin
  tables fit a 390 px screen; the score bar fill was never visible (inline span) — fixed, and
  the score is a coloured pill. 7 new tests, 361 in total.

## Next (backlog)
- Move hosting off the laptop to an always-on server with `deploy/docker-compose.yml`
  (`docs/DEPLOY.md`, AGENTS task 0), then deploy the current code there so real users can sign up (admin opens registration on the users page).
- Open ideas: proactive RPM limiter, OCR (out of scope).
- F5 Market feedback on the vacancy (explicitly out of scope for now).
- Optional: proactive client-side RPM limiter to avoid free-tier 429s entirely; OCR for
  scanned PDFs if it ever becomes in scope.

## Known issues / notes
- Gemini models: `gemini-2.5-flash` is no longer available to new keys. `gemini-3.5-flash`
  (the old fallback) is not on the current pricing page; use `gemini-3.5-flash-lite` with
  `gemini-3.1-flash-lite` as fallback. On the free tier `gemini-3.8-flash` is mostly 429/503.
- The Gemini paid tier may ask for a $30 first prepayment ("for security reasons") on new
  billing accounts; that is why DeepSeek was added.
- DeepSeek has no response schemas: outputs are validated by pydantic and retried once, so
  a model that often ignores the schema shows up as `invalid_output` failures.
- DeepSeek prices are doubled at peak hours (01:00–04:00 and 06:00–10:00 UTC, weekdays).
- Login rate limiting lives in memory: it resets when the app restarts (fine for one
  process per database, see AGENTS.md).
- PDF export: emoji and CJK characters are not in DejaVu Sans and come out blank (a
  warning is logged). RU/KK/EN text is fully covered.
- The first live DeepSeek call once ran for over 3 minutes before succeeding on a rerun.
  DeepSeek may hold requests under load (the client timeout is 300 s), so a slow first
  resume is not necessarily an error.
