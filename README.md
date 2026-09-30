# Enbek Screen — AI bulk resume screening (RU/KK)

An HR tool that screens many resumes at once against one or more vacancies and returns a
ranked Top-N shortlist — with fairness built in: demographics are stripped before scoring,
vacancy texts are checked for biased wording, and shortlists get aggregate diversity metrics.

Rebuilt from the Enbek prototype (single-person career advice form); the original code is
kept read-only in [`legacy/`](legacy/).

## Features

| ID | Feature |
|----|---------|
| F1 | Batch upload — drag and drop up to 50 files, live progress, clear list of rejected files |
| F2 | Ranking per vacancy — Top-5/10/20/all (ties kept), score bars, reasons, CSV export for Excel |
| F3 | Session history — every screening is saved in SQLite and can be reopened |
| F4 | `.pdf` (text layer), `.docx`, `.txt` parsing — no OCR, safe against malformed files |
| F6 | Append — add resumes or vacancies later; only new work is sent to the AI |
| F7 | Gemini engine — structured JSON extraction + fit scoring, validated, retried, rate-limit aware |
| B1 | Bias check — RU/KK/EN lexicon + AI review with explanations and a neutral rewrite |
| B2 | Diversity — shortlist vs pool by gender and age band, four-fifths rule, small-cell suppression |

UI in Russian and Kazakh, light/dark theme, responsive down to phone width.

## Quick start

Requirements: Python 3.11+ and a Gemini API key from <https://aistudio.google.com/apikey>.

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env        # then put your key into .env
.\.venv\Scripts\python.exe scripts\smoke_test.py   # one real call: key + model OK?
.\.venv\Scripts\flask.exe --app app run --debug
```

**macOS / Linux**
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then put your key into .env
python scripts/smoke_test.py
flask --app app run --debug
```

Open <http://127.0.0.1:5000>, create a screening, and drop the files from
[`samples/resumes/`](samples/resumes/) to try it (all people in the samples are fictional).

## Configuration

All settings come from environment variables (or `.env`, which is git-ignored).

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMINI_API_KEY` | — (required) | Gemini API key |
| `GEMINI_MODEL` | — (required) | Main model, e.g. `gemini-3.8-flash` |
| `GEMINI_FALLBACK_MODEL` | empty | Used while the main model is rate-limited or overloaded |
| `GEMINI_CONCURRENCY` | `2` | Parallel Gemini requests (keep low on the free tier) |
| `GEMINI_MAX_RETRIES` | `10` | Retries on 429/5xx before a resume is marked failed |
| `FLASK_SECRET_KEY` | generated | Session secret; generated into `instance/.secret_key` if unset |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS |

`python scripts/smoke_test.py --list-models` lists the models your key can use.

## Commands

| Task | Command |
|------|---------|
| Run (dev) | `flask --app app run --debug` |
| Run (production) | `waitress-serve --host 0.0.0.0 --port 8000 --call app:create_app` |
| Tests | `pytest -q` (Gemini is mocked, no key needed) |
| Smoke test | `python scripts/smoke_test.py [--pipeline \| --bias \| --list-models]` |
| Regenerate samples | `pip install -r requirements-dev.txt && python scripts/make_samples.py` |

## Docker

```bash
docker build -t enbek-screen .
docker run -p 8000:8000 --env-file .env -v enbek-data:/app/instance enbek-screen
```

The image runs as a non-root user, keeps the SQLite database in the `/app/instance`
volume and has a health check on `/healthz`. Run exactly **one** container/process per
database: background analysis runs inside the web process.

## How it works

```
upload ─► parsing (F4) ─► resumes: pending
                              │  background job, ≤ GEMINI_CONCURRENCY requests
                              ▼
            Gemini extraction ─► profile  +  demographics (separate table)
                              │
          anonymize.scoring_view (no name, contacts, demographics, dates)
                              ▼
            Gemini scoring vs every vacancy ─► scores ─► ranking / Top-N / CSV (F2)
                                                     └─► diversity metrics (B2)
vacancy text ─► lexicon + optional Gemini review ─► bias report (B1)
```

- **Two AI calls per resume.** Extraction sees the raw text; scoring sees only the
  anonymized professional profile, so the score cannot depend on gender, age, marital
  status, nationality, photo, name or contacts.
- **Reliability.** JSON mode with a response schema, pydantic validation, one retry on
  invalid output, then `analysis_failed` without stopping the batch. 429/503 put that model
  on a shared cooldown and traffic switches to the fallback model immediately.
- **Append-only.** Files are de-duplicated by SHA-256 per session; adding a vacancy scores
  only the missing (resume, vacancy) pairs.

Detailed specs for every feature are in [`docs/specs/`](docs/specs/); status and known
issues in [`PROGRESS.md`](PROGRESS.md).

## Security and privacy

- Secrets only from the environment; the key is never logged or printed.
- CSRF tokens on every state-changing request; strict Content-Security-Policy (no inline
  scripts or styles), `X-Frame-Options: DENY`, `nosniff`, restrictive referrer and
  permissions policies, HSTS over HTTPS, `HttpOnly` + `SameSite=Lax` cookies.
- Uploads: size and count limits, extension **and** content-signature checks, zip-bomb
  and page limits, processing in memory — original files are never stored, only text.
- Resume and vacancy text is treated as untrusted in prompts (fenced, instructions inside
  it are ignored) and all output is validated against schemas.
- Parameterized SQL only; Jinja auto-escaping; CSV export neutralizes formula injection.
- Demographics are stored separately, used only for aggregates with small-cell
  suppression, and never shown per candidate or exported.

## Project layout

```
app/
  __init__.py          create_app(), error handlers
  config.py  i18n.py  security.py
  routes/              core.py, sessions.py, screening.py, vacancies.py
  services/            parsing, gemini, anonymize, scoring, storage, jobs, bias, diversity
  prompts/             Gemini prompt templates
  templates/  static/
docs/specs/            one spec per feature
samples/               synthetic RU/KK resumes, vacancies and bad files
scripts/               smoke_test.py, make_samples.py
tests/                 pytest suite (Gemini mocked)
legacy/                original Enbek prototype, read-only
```

## Troubleshooting

- **Analysis is slow / "Gemini is overloaded".** The free tier allows only a few requests
  per minute; each resume needs two. Resumes wait and retry automatically; press
  "Retry failed" later for any that still failed, or use a paid key.
- **`404 ... model is no longer available`.** Pick a current model from
  `python scripts/smoke_test.py --list-models` and set `GEMINI_MODEL`.
- **A PDF is rejected as a scan.** Only PDFs with a text layer are supported (no OCR);
  export the resume to PDF from a text editor or upload the DOCX.
