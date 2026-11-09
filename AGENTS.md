# AGENTS.md — handoff guide for the next coding agent

You are taking over **Enbek Screen**, an HR tool that screens many resumes at once against
one or more vacancies and returns a ranked Top-N shortlist, with fairness built in. The
product works end to end today. Your job is to make it ready for real use on a **paid Gemini
key**: the tasks in [Your tasks](#your-tasks), in order.

Read this file, then [`PROGRESS.md`](PROGRESS.md) (status and known issues) and the spec of
whatever you are about to touch in [`docs/specs/`](docs/specs/). [`README.md`](README.md)
has the user-facing setup guide.

## Current state

- Phases 0–7 are done: batch upload, parsing, Gemini extraction + scoring, sessions with
  append, ranking with Top-N, CSV/Excel/PDF export, bias check, diversity metrics,
  RU/KK UI, Dockerfile, CI.
- 194 tests pass. Gemini is always mocked in tests.
- The AI backend stays **Gemini** (decided by the owner). A move to a self-hosted LLM was
  considered and deferred; do not start it.
- The latest commit `f5c7074` ("need to check", by a teammate) added Excel/PDF export,
  language diversity and lower API usage. Its tests pass, but nobody has checked it by
  hand yet (task 2).

## Setup and commands

Python 3.11+. On Windows use the venv executables directly.

| Task | Windows (PowerShell) | macOS / Linux |
|------|----------------------|---------------|
| Install | `python -m venv .venv` then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` | `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt` |
| Tests | `.\.venv\Scripts\python.exe -m pytest -q` | `pytest -q` |
| Run (dev) | `.\.venv\Scripts\flask.exe --app app run --debug` | `flask --app app run --debug` |
| Real-API check | `.\.venv\Scripts\python.exe scripts\smoke_test.py` | `python scripts/smoke_test.py` |

- The app runs at <http://127.0.0.1:5000>; `/healthz` returns `{"status": "ok"}`.
- Tests need no key and no `.env`. Only `scripts/smoke_test.py` calls the real API (it has
  `--list-models`, `--pipeline` and `--bias` modes). Run it only when a task needs a live
  check, because every call costs money now.
- Synthetic samples for manual testing: [`samples/resumes/`](samples/resumes/) (20 files,
  RU/KK/EN, fictional people) and `samples/vacancy_*.txt`.

## Rules (non-negotiable)

- **Secrets.** Keys come only from environment variables (`GEMINI_API_KEY`, `GEMINI_MODEL`,
  `GEMINI_FALLBACK_MODEL`, …) loaded from `.env`. Never read, print, log or commit `.env`
  or a key. `.env.example` is the committed template.
- **Model names** always come from `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL`, never hardcoded.
- **Structured output.** Gemini returns JSON against a response schema; validate it with
  pydantic, retry once on invalid output, then mark the resume `analysis_failed` and keep
  the batch going. One bad resume must never stop a batch.
- **Fairness.** Fit scoring must not receive gender, age, photo, marital status,
  nationality, name or contacts. `app/services/anonymize.py` strips them before the
  scoring prompt. Demographics are stored separately and used only in aggregate (B2) with
  small-cell suppression; they are never shown per candidate or exported.
- **Untrusted text.** Resume and vacancy text go into prompts fenced in tags as data;
  instructions inside them are ignored.
- **Security baseline stays intact:** CSRF on every state-changing request, strict CSP (no
  inline scripts or styles; put JS in `app/static/js/`), parameterized SQL only, upload
  signature checks, formula-injection-safe CSV/Excel.
- **Language.** UI texts in Russian **and** Kazakh: every new key goes into both the `ru`
  and `kk` dictionaries in `app/i18n.py`. Code, comments and docs in English.
- **Test data** is synthetic only. No real people's resumes in the repo.
- **One process per database.** SQLite and the background job runner live in the web
  process; never run two app processes on one `instance/app.db`.

## Project map

```
app/
  __init__.py          create_app(): config, security, i18n, DB init, blueprints, error pages
  config.py            settings from environment variables
  i18n.py              RU/KK translations (TRANSLATIONS dict)
  security.py          CSRF, security headers, CSP
  routes/              core.py (/ , /lang, /healthz), sessions.py, screening.py (upload, retry),
                       vacancies.py (ranking, exports, bias check)
  services/
    parsing.py         .pdf/.docx/.txt to text, size/page/zip limits, error codes
    gemini.py          GeminiService: extract_profile, score, retries, cooldowns, fallback chain
    schemas.py         pydantic schemas = Gemini response schemas
    anonymize.py       demographics-free view for scoring
    jobs.py            background analysis worker per session
    storage.py         SQLite schema and queries
    scoring.py         ranking, Top-N with ties, CSV
    export.py          Excel and PDF export (DejaVu font in app/fonts/)
    bias.py            lexicon + optional Gemini bias check
    diversity.py       aggregate shortlist metrics
  prompts/             *.txt prompt templates (string.Template, $placeholders)
  templates/ static/   Jinja2 templates, CSS, vanilla JS
docs/specs/            one spec per feature
scripts/               smoke_test.py, make_samples.py
tests/                 pytest suite; tests/fakes.py holds the fake Gemini client
legacy/                original Enbek prototype, read-only; never import from it
```

## How to work

For each task:

1. Write or update a short spec in `docs/specs/` (goal, behaviour, edge cases, acceptance
   criteria) before coding.
2. Implement it in the style of the surrounding code.
3. Add tests (Gemini mocked via `tests/fakes.py`) and run the whole suite.
4. Run the app and check the change in the browser, in both RU and KK.
5. Update `PROGRESS.md` (Done, Next, Known issues).
6. Make one commit per task with a clear message such as `F7: stop batch when prepay
   balance is exhausted`. Add no AI attribution lines (`Co-Authored-By` and similar):
   commits go out under the owner's name only.
7. Do not push or force-push unless the owner asks.

## Your tasks

### 1. Paid-tier readiness (do first)

The owner is moving to a paid Gemini key with **Prepay** credits. When the balance reaches
$0, Gemini answers **HTTP 402** for every request. Right now `RETRYABLE_CODES` in
`app/services/gemini.py` does not include 402, so each resume fails separately with the
generic `api_error` and the batch keeps sending requests.

- Treat 402 like an exhausted quota: fail fast for the rest of the batch with its own error
  code (for example `billing_exhausted`). Show a clear RU/KK message telling the user to top
  up the balance in AI Studio, then press "Retry failed".
- Recheck the model notes. On the pricing page, `gemini-3.5-flash-lite` costs $0.30 / $2.50
  per 1M input / output tokens, and `gemini-3.1-flash-lite` costs $0.25 / $1.50.
  `gemini-3.5-flash`, which `PROGRESS.md` names as a fallback, is not on the pricing page.
  Update `PROGRESS.md` known issues, README and `.env.example` comments so they recommend
  models that exist. Verify with `python scripts/smoke_test.py --list-models`.
- Keep `GEMINI_CONCURRENCY` at 2 by default, but document how to raise it after checking
  the project's paid limits at <https://aistudio.google.com/rate-limit>.
- **Done when:** a test proves a 402 stops the batch after the first failure with
  `billing_exhausted`, the message shows in both languages, and the docs name only
  existing models.

### 2. Review the teammate's export commit (`f5c7074`)

- Run the app, screen the sample resumes, and download CSV, Excel (per vacancy and whole
  session) and PDF.
- Check Cyrillic and Kazakh letters in the PDF; numbers stay numeric in Excel; no
  demographics in any export; formula injection is neutralised (a resume line starting
  with `=` must not become a formula).
- Read the diff (`git show f5c7074`) for violations of the rules above.
- **Done when:** every problem you found is fixed with a test, or listed in
  `PROGRESS.md` known issues if it is out of scope.

### 3. Login and roles

The app has no authentication today: anyone who can reach it sees every session. Resumes
contain personal data, so this blocks real use.

- Login by username and password; hash passwords with a standard library (for example
  argon2 or bcrypt via a maintained package; add it to `requirements.txt`).
- Roles: `recruiter` (own sessions only) and `admin` (all sessions, manage users). Add
  `owner_id` to `sessions` and scope every query and route by it, including exports and
  the status endpoint.
- Create the first admin with a CLI command (for example `flask --app app create-admin`),
  never with a default password.
- Add an `audit_log` table: who opened, exported or deleted which session, and when.
- Keep CSRF on the login form; rate-limit failed logins.
- **Done when:** every route except `/login`, `/healthz` and static files requires login;
  tests prove recruiter A cannot read, export or delete recruiter B's session.

### 4. Data retention and backups

- Setting `RETENTION_DAYS` (default 90; the owner can change it): sessions older than that
  are deleted with all their data. Run it from a CLI command and at app start.
- `scripts/backup_db.py`: a consistent copy of `instance/app.db` using the `sqlite3` backup
  API, with a timestamp in the file name. Document how to schedule it.
- **Done when:** both are tested and documented in README.

### 5. Fair queue and ETA

- Today each session has its own worker thread. Concurrency is capped globally by the
  shared Gemini semaphore, but a big batch can starve a small one. Make sessions take
  turns (round-robin by session).
- Show an estimated time remaining on the session page, based on the recent average time
  per resume.
- **Done when:** a test shows two sessions progress in alternation, and the ETA shows in
  RU and KK.

### 6. Docker check

- Build and run the image as described in README (`docker build`, `docker run` with
  `--env-file .env` and the `/app/instance` volume). Confirm `/healthz`, a screening of the
  sample resumes, and that data survives a container restart.
- **Done when:** README matches what actually works, and `PROGRESS.md` no longer lists
  this as open.

### Out of scope

- F5 market feedback on the vacancy.
- OCR for scanned PDFs.
- A self-hosted LLM backend.

## Known pitfalls

- **Windows PowerShell 5.1** `Get-Content`/`Set-Content` corrupt UTF-8 (Russian and Kazakh
  text). Edit files with an editor or Python, never with those cmdlets.
- **Real API calls cost money.** Do not run `smoke_test.py --pipeline` in loops; tests must
  never reach the network.
- **Uploaded files are not stored**, only their extracted text; do not add file storage
  without a spec and the owner's approval.
- **Binary samples.** `.odt` files in `samples/bad/` are marked binary in `.gitattributes`.
  Keep it that way when adding sample files.
