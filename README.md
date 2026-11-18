# Enbek Screen — AI bulk resume screening (RU/KK)

An HR tool that screens many resumes at once against one or more vacancies and returns a
ranked Top-N shortlist — with fairness built in: demographics are stripped before scoring,
vacancy texts are checked for biased wording, and shortlists get aggregate diversity metrics.

Rebuilt from the Enbek prototype (single-person career advice form); the prototype code was
removed from the tree and stays in the git history (before commit `4466bb2`).

> **Команде:** что сделать на сервере после обновления и как собрать пользователей для
> зачёта — в [`TEAMMATE.md`](TEAMMATE.md) (на русском).

## Features

| ID | Feature |
|----|---------|
| F1 | Batch upload — drag and drop up to 50 files, live progress, clear list of rejected files |
| F2 | Ranking per vacancy — Top-5/10/20/all (ties kept), score bars, reasons, CSV export for Excel |
| US7 | Excel (.xlsx, one sheet per vacancy) and PDF shortlist report with Cyrillic/Kazakh font; whole-session Excel export |
| F3 | Session history — every screening is saved in SQLite and can be reopened |
| F4 | `.pdf` (text layer), `.docx`, `.txt` parsing — no OCR, safe against malformed files |
| F6 | Append — add resumes or vacancies later; only new work is sent to the AI |
| F7 | AI engine (DeepSeek or Gemini) — structured JSON extraction + fit scoring, validated, retried, rate-limit aware |
| B1 | Bias check — RU/KK/EN lexicon + AI review with explanations and a neutral rewrite |
| B2 | Diversity — shortlist vs pool by gender, age band and resume language, four-fifths rule, small-cell suppression |
| A1 | Login and roles — recruiters see only their own sessions, admins see all and manage users; audit log |
| S1 | Usage statistics for admins (users, sign-ins, sessions, resumes, exports by day; Excel report) and sign-up with an invite code |
| S2 | Admin switches for sign-up (open / with invite code / closed), email on sign-up, daily resume limit per recruiter |
| S3 | Example session with a Python vacancy for every new user; any vacancy can be deleted |
| S5 | “Подходят” tile opens the suitable candidates; mobile layout (top bar, cards for admin tables); coloured score pill and visible bar |
| CR-01 | Candidate pipeline — status per candidate (new, shortlist, interview, offer, rejected) with a note, status filter, name search; status and note in all exports |
| Q1 | Fair queue — sessions take turns resume by resume; the session page shows the time remaining |
| SEC-02a | Personal data is removed from the resume text on the server before anything goes to the AI provider |
| R1 | Data retention (sessions untouched for `RETENTION_DAYS` are deleted) and consistent database backups |

UI in Russian and Kazakh, light/dark theme, responsive down to phone width.

## Getting started

You need Python 3.11+, Git and an API key for one AI provider:

- **DeepSeek** (recommended, cheapest): sign in at <https://platform.deepseek.com>, create
  an API key there and top up the balance at <https://platform.deepseek.com/top_up>.
- **Gemini**: a key from <https://aistudio.google.com/apikey> (free tier ≈ 10 resumes a day).

### 1. Get the code

```bash
git clone https://github.com/zhaqsylyqq/resume_analyzer.git
cd resume_analyzer
```

### 2. Install dependencies

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --no-deps -r requirements-dev.lock
```

**macOS / Linux**
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes --no-deps -r requirements-dev.lock
```

`requirements.lock` (server) and `requirements-dev.lock` (development and tests) pin every
package with its hash. After changing `requirements.txt` or `requirements-dev.txt`, regenerate
both with `uv pip compile <file> --universal --python-version 3.12 --generate-hashes -o <lock>`.

### 3. Add your API key

Copy the settings template:

```powershell
Copy-Item .env.example .env        # Windows (PowerShell)
```
```bash
cp .env.example .env               # macOS / Linux
```

Open `.env` and set the provider and its key. For DeepSeek:

```
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=<your key>
```

For Gemini, set `LLM_PROVIDER=gemini`, `GEMINI_API_KEY` and `GEMINI_MODEL` instead. Never
commit this file; it is already git-ignored. See [Configuration](#configuration) for the
optional settings.

### 4. Check the key (optional)

This makes one real AI call and reports whether the provider, key and model work. To see
which models your key can use, add `--list-models`.

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py   # Windows (PowerShell)
```
```bash
python scripts/smoke_test.py                       # macOS / Linux
```

### 5. Create the first admin

There is no default user or password. Create an admin once (password input is hidden and
asked twice; at least 10 characters):

```powershell
.\.venv\Scripts\flask.exe --app app create-admin    # Windows (PowerShell)
```
```bash
flask --app app create-admin                       # macOS / Linux
```

More users (recruiters or admins) are added in the app under **Пользователи** (admins only).
To let classmates or pilot users sign themselves up, open **Пользователи → Регистрация** and
tick **Разрешить самостоятельную регистрацию**. With **Требовать код приглашения** ticked
people also need the code you share; unticked, a username, email and password are enough.
The same block sets the **daily resume limit per recruiter** (default 30, `0` = none), which
protects the prepaid AI balance. Changes apply at once, no restart.

### 6. Start the app

```powershell
.\.venv\Scripts\flask.exe --app app run --debug    # Windows (PowerShell)
```
```bash
flask --app app run --debug                        # macOS / Linux
```

Open <http://127.0.0.1:5000> and sign in. Press `Ctrl+C` in the terminal to stop the server.

### 7. Try a screening

1. Click **Новый скрининг** (**Жаңа іріктеу** in Kazakh).
2. Enter a job title and paste a vacancy text, for example from
   [`samples/vacancy_python_ru.txt`](samples/vacancy_python_ru.txt).
3. Upload files from [`samples/resumes/`](samples/resumes/). All people in the samples are
   fictional.
4. Wait for the analysis to finish, then open the ranking to choose a Top-N and export
   Excel, PDF or CSV.

### Run the tests

Tests mock the AI provider, so they need no key and no `.env`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q            # Windows (PowerShell)
```
```bash
pytest -q                                          # macOS / Linux
```

CI also runs lint, type and coverage checks. Run them locally before pushing:

```bash
ruff check .            # lint (security rules included)
mypy                    # types for app/ and scripts/
pytest -q --cov         # tests; fails if coverage of app/ drops below 90 %
pre-commit install      # optional: run ruff and mypy on every commit
```

## Configuration

All settings come from environment variables (or `.env`, which is git-ignored). They are
checked once at start-up: a wrong value (text where a number is expected, an unknown provider,
a negative limit…) stops the app with a list of every problem instead of being silently
replaced by a default. Check the configuration without starting anything:

```bash
python -m app.check_config      # prints every setting, secrets only as "set" / "not set"
```

Secrets (`FLASK_SECRET_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `INVITE_CODE`, `BACKUP_KEY`) can also be
read from a file: set `NAME_FILE=/path/to/file` instead of `NAME` (Docker / Kubernetes secrets).

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_ENV` | `development` | `production` makes the AI key and HTTPS-only cookies mandatory and never generates a new session secret; `development`, `test` |
| `DATA_DIR` | `instance/` | Folder for the SQLite database and the generated session secret |
| `ADMIN_MFA_REQUIRED` | `0` | `1` = admins must switch on two-factor sign-in before using the app |
| `SHUTDOWN_TIMEOUT` | `25` | Seconds a stopping server waits for resumes being analyzed (keep below the orchestrator's grace period) |
| `LLM_PROVIDER` | `gemini` | AI provider: `deepseek` or `gemini` |
| `DEEPSEEK_API_KEY` | — (required for DeepSeek) | DeepSeek API key |
| `DEEPSEEK_MODEL` | `deepseek-flash` | DeepSeek model (thinking mode is switched off to save tokens) |
| `DEEPSEEK_FALLBACK_MODEL` | empty | Optional comma-separated fallback chain, as for Gemini |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API address; change only for a proxy |
| `GEMINI_API_KEY` | — (required for Gemini) | Gemini API key |
| `GEMINI_MODEL` | — (required for Gemini) | Main model, e.g. `gemini-3.5-flash-lite` |
| `GEMINI_FALLBACK_MODEL` | empty | Comma-separated chain tried while the main model is rate-limited, overloaded or out of daily quota |
| `GEMINI_CONCURRENCY` | `2` | Parallel AI requests, for either provider |
| `GEMINI_MAX_RETRIES` | `5` | Retries on 429/5xx before a resume is marked failed, for either provider |
| `FLASK_SECRET_KEY` | generated | Session secret (at least 32 characters); outside production it is generated into `instance/.secret_key` if unset |
| `SESSION_COOKIE_SECURE` | `0`; `1` in production | Cookies only over HTTPS; cannot be `0` in production |
| `INVITE_CODE` | empty | Initial invite code; until an admin saves the registration settings, a non-empty code opens sign-up with this code |
| `DAILY_RESUME_LIMIT` | `30` | Initial limit of resumes per recruiter per 24 hours (0 = none); admins change it in the app |
| `DAILY_AI_CHECK_LIMIT` | `20` | Initial limit of AI vacancy checks per recruiter per 24 hours (0 = none); the dictionary check always runs; admins change it in the app |
| `BACKUP_OFFSITE_DIR` | empty | Folder outside this disk for encrypted backups (`flask --app app backup-offsite`) |
| `BACKUP_KEY` | empty | Key for encrypted backups (checked at start-up); create with `flask --app app generate-backup-key` |
| `STATS_UTC_OFFSET` | `5` | Hours added to UTC when statistics group events by day (Kazakhstan: 5) |
| `STARTER_SESSION` | `1` | Give every new user an example session with a Python vacancy (`0` = off) |
| `TRUSTED_PROXIES` | `127.0.0.1,::1` | Peers allowed to pass the visitor IP (`CF-Connecting-IP` / `X-Forwarded-For`) and the scheme (`X-Forwarded-Proto`); needed for rate limits and HSTS behind Cloudflare Tunnel |
| `RETENTION_DAYS` | `90` | Sessions not changed for this many days are deleted with all their data; `0` keeps everything |

`python scripts/smoke_test.py --list-models` lists the models your key can use.

## Commands

| Task | Command |
|------|---------|
| Run (dev) | `flask --app app run --debug` |
| Create an admin | `flask --app app create-admin` |
| Delete old sessions now | `flask --app app purge-old [--days N]` |
| Back up the database | `flask --app app backup [--out DIR] [--keep N]` |
| Encrypted copy outside this disk | `flask --app app backup-offsite [--out DIR] [--keep N]` |
| Same, only when the USB drive is in and the copy is old (scheduled task) | `flask --app app backup-auto` |
| Prove a copy can be restored | `flask --app app restore-check [FILE] [--restore-to PATH]` |
| Check the live database and the queue | `flask --app app db-check` |
| New backup key | `flask --app app generate-backup-key` |
| Switch off 2FA for a locked-out user | `flask --app app reset-2fa USER` |
| One-time password reset link | `flask --app app reset-link USER [--base-url URL]` |
| Check the configuration | `python -m app.check_config` (or `flask --app app config-check`) |
| Run (production) | `waitress-serve --host 0.0.0.0 --port 8000 --call app:create_server_app` |
| Tests | `pytest -q` (Gemini is mocked, no key needed) |
| Lint and types | `ruff check .` and `mypy` |
| Smoke test | `python scripts/smoke_test.py [--pipeline \| --bias \| --list-models]` |
| Regenerate samples | `python scripts/make_samples.py` |

Service commands (APP-09) read the same validated configuration as the server, so
`DATA_DIR`, `*_FILE` secrets and the production rules apply to them too. Every one exits
with code 0 on success and 1 with a one-line reason otherwise. In Docker run them in the
container, e.g. `docker compose -f deploy/docker-compose.yml exec app flask --app app backup`.
`scripts/backup_db.py`, `backup_offsite.py` and `restore_check.py` still work (the scheduled
task uses them); they now call the same code in `app/services/backup.py`.

## API usage (keeping the AI bill small)

| What | AI requests |
|------|-----------------|
| New resume | 2: profile extraction + one scoring call for **all** vacancies of the session |
| Vacancy added later | 1 per resume (scoring only; the stored profile is reused) |
| Re-opening a session, ranking, Top-N | 0 (everything is read from SQLite) |
| Duplicate file | 0 (detected by SHA-256 before analysis) |
| Bias check | 0 for the dictionary; 1 only when the user clicks "check with AI" |
| Diversity metrics, CSV / Excel / PDF export | 0 (computed locally) |

Prompts send compact JSON, resumes are cut at 20 000 characters and tests always use a
fake client, so `pytest` never spends quota.

## Usage statistics

Admins open **Статистика** (`/admin/stats`): registered users, users who signed in, active
users (created a screening session), sessions, analyzed resumes and exports, for all time
or the last 7 / 30 days, with a chart by day and a table per user. **Скачать отчёт Excel**
saves the same data plus the full audit log for the period, ready to hand in as proof of
usage. Numbers come from the audit log, so they include sessions later removed by
retention.

## Data retention and backups

**Retention.** Resumes are personal data, so sessions that nobody changed for
`RETENTION_DAYS` days (default 90) are deleted with everything in them. This runs when the
app starts, once a day while it runs, and on demand with `flask --app app purge-old`.
Every deletion is recorded in the audit log as "Автоудаление".

**Backups.** `scripts/backup_db.py` makes a consistent copy of `instance/app.db` (SQLite
online backup API, safe while the app is running), checks it and keeps the newest 14 in
`instance/backups/` (`--keep N` to change, `--out DIR` for another folder). Keep copies on
another disk or cloud folder as well; a backup on the same disk does not survive a disk
failure. To restore, stop the app and copy a backup over `instance/app.db`.

**Encrypted copy outside the computer (QW-01).** `scripts/backup_offsite.py` makes the same
consistent snapshot, encrypts it (AES-256-GCM, key `BACKUP_KEY`) and writes it to
`BACKUP_OFFSITE_DIR` — a USB drive, a NAS or a synced folder of a storage provider that keeps
data in Kazakhstan (resumes are personal data: no foreign cloud drives). It keeps the newest
30 copies. `scripts/restore_check.py` decrypts the newest copy into a temporary file, checks
it and prints the row counts; `--restore-to PATH` writes the restored database.

```powershell
.\.venv\Scripts\python.exe scripts\backup_offsite.py --generate-key   # once; put it into .env as BACKUP_KEY
.\.venv\Scripts\python.exe scripts\backup_offsite.py                  # BACKUP_OFFSITE_DIR from .env
.\.venv\Scripts\python.exe scripts\restore_check.py
powershell -ExecutionPolicy Bypass -File scripts\register_backup_task.ps1   # automatic, see below
```

**The USB drive does not have to stay plugged in.** The scheduled task runs
`flask --app app backup-auto` every 30 minutes and at sign-in, without a window and on battery
too: when the drive is connected and the newest copy is older than 20 hours, it makes a copy
and verifies it; when there has been no verified copy for 3 days, a Windows notification asks
to plug the drive in (at most once a day), and a failed backup also shows one. Plugging the
drive in for a few minutes every couple of days is enough. Another drive letter is fine: any
drive with a `hrscreen-backups` folder at its root is used.

Store `BACKUP_KEY` in a password manager with two people: without it no copy can be read.

Schedule the plain local backup daily:

- **Windows** (Task Scheduler, once, in PowerShell from the project folder):
  ```powershell
  schtasks /Create /TN "Enbek Screen backup" /SC DAILY /ST 02:00 /TR "\"$PWD\.venv\Scripts\python.exe\" \"$PWD\scripts\backup_db.py\""
  ```
- **Linux / macOS** (`crontab -e`):
  ```
  0 2 * * * cd /path/to/resume_analyzer && .venv/bin/python scripts/backup_db.py
  ```
- **Docker**: `docker exec <container> python scripts/backup_db.py`, then copy
  `/app/instance/backups` out of the volume.

## Docker

```bash
docker build -t enbek-screen .
docker run -d --name enbek -p 8000:8000 --env-file .env -v enbek-data:/app/instance enbek-screen
docker exec -it enbek flask --app app create-admin      # once: first admin
```

Open <http://127.0.0.1:8000> and sign in.

- The image runs as the non-root user `app` with
  [waitress](https://docs.pylonsproject.org/projects/waitress/) on port 8000 and has a
  health check on `/healthz` (`docker ps` shows `healthy`). `/readyz` answers 503 while the
  database is unavailable or the server is stopping; `docker stop -t 30` lets the analysis in
  progress finish (APP-08).
- The SQLite database, the generated session secret and backups live in the
  `/app/instance` volume, so data survives `docker restart` and replacing the container
  with a new image (`docker rm -f enbek` + the same `docker run`).
- Backups: `docker exec enbek python scripts/backup_db.py` writes to
  `/app/instance/backups`; copy them out with `docker cp enbek:/app/instance/backups .`.
- Run exactly **one** container per database: the analysis queue runs inside the web
  process.
- Behind a corporate proxy that intercepts TLS, `pip install` during `docker build` needs
  the proxy's CA certificate in the image; the plain build needs nothing extra.

Verified on 2026-11-10 (Docker 29): build, `/healthz`, login required, `create-admin`,
screening of the 20 sample resumes against 2 vacancies through the DeepSeek code path
(an OpenAI-compatible stand-in server, no real key in the check), Excel/PDF export, audit
log, backup inside the container, and data intact after `docker restart` and after
recreating the container on the same volume.

## How it works

```
upload ─► parsing (F4) ─► resumes: pending
                              │  shared worker pool, sessions take turns (Q1)
                              ▼
            pii.redact (local): name, contacts, demographics ─► kept here, never sent
                              │  redacted text only
                              ▼
            AI extraction ─► profile  +  demographics (separate table)
                              │
          anonymize.scoring_view (no name, contacts, demographics, dates)
                              ▼
            Gemini scoring vs every vacancy ─► scores ─► ranking / Top-N / CSV (F2)
                                                     └─► diversity metrics (B2)
vacancy text ─► lexicon + optional Gemini review ─► bias report (B1)
```

- **Two AI calls per resume, no personal data in either (SEC-02a).** Before extraction,
  `app/services/pii.py` removes the name, e-mails, phones, IIN and document numbers, links,
  birth date and age, gender, family status, nationality, street address and referees'
  names, and replaces them with markers such as `[EMAIL]`. Name, contacts and demographics
  are taken from the text locally. Scoring then sees only the anonymized professional
  profile, so the score cannot depend on gender, age, marital status, nationality, photo,
  name or contacts. Limits: [`docs/specs/SEC-02a-redaction.md`](docs/specs/SEC-02a-redaction.md).
- **Reliability.** JSON mode with a response schema, pydantic validation, one retry on
  invalid output, then `analysis_failed` without stopping the batch. 429/503 put that model
  on a shared cooldown and traffic switches to the fallback model immediately.
- **Fair queue.** One pool of `GEMINI_CONCURRENCY` workers serves all sessions in turn,
  one resume at a time, so a small batch is not stuck behind a big one. The session page
  shows the time remaining, estimated from the last 20 analyzed resumes.
- **Append-only.** Files are de-duplicated by SHA-256 per session; adding a vacancy scores
  only the missing (resume, vacancy) pairs.

Detailed specs for every feature are in [`docs/specs/`](docs/specs/); status and known
issues in [`PROGRESS.md`](PROGRESS.md).

## Security and privacy

- Secrets only from the environment; the key is never logged or printed.
- Login required on every page except `/login`, `/healthz` and `/readyz`. Passwords are hashed with
  Argon2id; failed logins are limited (5 per 15 minutes per username and per IP); the
  session is renewed on login and expires after 12 hours. Recruiters see only their own
  sessions (other sessions answer 404); admins see all and manage users. Opening,
  exporting and deleting a session is written to an audit log (`/admin/audit`).
- Two-factor sign-in (SEC-01): any user can switch it on in **Profile** (click your name):
  a 6-digit code from an authenticator app (RFC 6238) after the password, plus 10 one-time
  recovery codes. `ADMIN_MFA_REQUIRED=1` makes it mandatory for admins. Users change their
  own password in Profile; a forgotten password is reset with a one-time link (valid 1 hour)
  that an admin creates on the Users page and passes on — there is no e-mail sending.
  Changing the password or switching 2FA on/off signs out the user's other sessions.
  Locked out completely: `flask --app app reset-2fa USER`, `flask --app app reset-link USER`.
- CSRF tokens on every state-changing request; strict Content-Security-Policy (no inline
  scripts or styles), `X-Frame-Options: DENY`, `nosniff`, restrictive referrer and
  permissions policies, HSTS over HTTPS, `HttpOnly` + `SameSite=Lax` cookies.
- Uploads: size and count limits, extension **and** content-signature checks, zip-bomb
  and page limits, processing in memory — original files are never stored, only text.
- Resume and vacancy text is treated as untrusted in prompts (fenced, instructions inside
  it are ignored) and all output is validated against schemas.
- Parameterized SQL only; Jinja auto-escaping; CSV and Excel exports neutralize formula injection.
- Demographics are stored separately, used only for aggregates with small-cell
  suppression, and never shown per candidate or exported.

## Project layout

```
app/
  __init__.py          create_app(), create_server_app() (resumes the queue), error handlers
  config.py  i18n.py  security.py
  routes/              core.py, sessions.py, screening.py, vacancies.py
  services/            parsing, gemini (AI engine + Gemini backend), openai_compat (DeepSeek
                       backend), anonymize, scoring, storage, jobs, bias, diversity
  prompts/             AI prompt templates
  templates/  static/
docs/specs/            one spec per feature
samples/               synthetic RU/KK resumes, vacancies and bad files
scripts/               smoke_test.py, make_samples.py, backup_db.py, backup_offsite.py,
                       restore_check.py, register_backup_task.ps1, serve_public.ps1
tests/                 pytest suite (AI provider mocked)
```

## Troubleshooting

- **"AI service balance is exhausted".** The provider answered HTTP 402: the prepaid balance
  is used up. The batch stops at once instead of calling an empty account. Top up
  (DeepSeek: <https://platform.deepseek.com/top_up>), then press "Retry failed".
- **"Daily limit exhausted".** The free tier allows about 20 requests per model per day,
  and each resume needs two — roughly 10 resumes per model per day. Every model in
  `GEMINI_FALLBACK_MODEL` has its own quota (the `-lite` models work well), so a chain of
  four models gives ~40 resumes/day. Quotas reset daily; for real volumes enable billing
  on the key. Press "Retry failed" once quota is available again.
- **Analysis is slow / "Gemini is overloaded".** Per-minute limits and 503s are retried
  automatically with the fallback chain; press "Retry failed" for anything left over.
- **`404 ... model is no longer available`.** Pick a current model from
  `python scripts/smoke_test.py --list-models` and set `GEMINI_MODEL`.
- **A PDF is rejected as a scan.** Only PDFs with a text layer are supported (no OCR);
  export the resume to PDF from a text editor or upload the DOCX.
