# Stage 0 of the enterprise roadmap — QW-01…QW-06

Urgent fixes from the architecture audit (2026-11-12, commit `4466bb2`), done without
changing the architecture: the app still runs as one process with SQLite. The full roadmap
(38 tasks) lives outside the repository; task IDs below match it.

| ID | What changed | Files |
|----|--------------|-------|
| QW-01 | Encrypted (AES-256-GCM) copy of the database to `BACKUP_OFFSITE_DIR`, a restore check, a daily Windows task | `scripts/backup_offsite.py`, `scripts/restore_check.py`, `scripts/register_backup_task.ps1`, `.env.example`, README |
| QW-02 | AI vacancy checks count against a daily per-recruiter quota (default 20, admin setting); over the quota only the dictionary check runs | `app/routes/vacancies.py`, `app/settings.py`, `app/services/storage.py` (table `llm_usage`), `app/templates/admin/users.html`, `app/i18n.py`, `app/static/js/session-form.js` |
| QW-03 | `requirements.lock` / `requirements-dev.lock` with hashes; pytest left the runtime list; Docker and CI install with `--require-hashes` | `requirements*.txt`, `requirements*.lock`, `Dockerfile`, `.github/workflows/ci.yml` |
| QW-04 | The scheme comes from `X-Forwarded-Proto` or Cloudflare's `CF-Visitor` (waitress strips `X-Forwarded-*`), but only from a peer in `TRUSTED_PROXIES`, so HSTS is sent and external URLs use https behind Cloudflare Tunnel; checked against a real waitress server | `app/security.py` (`TrustedProxyScheme`), `app/auth.py` |
| QW-05 | A restart no longer strands resumes: the server entry point `app:create_server_app` queues every session with pending resumes; CLI commands never start workers | `app/__init__.py`, `app/services/jobs.py` (`resume_pending`), `Dockerfile`, `scripts/serve_public.ps1` |
| QW-06 | Tunnel id removed from the docs, `legacy/` removed (kept in git history), `SECURITY.md` added | `docs/DEPLOY.md`, `README.md`, `AGENTS.md`, `.dockerignore`, `SECURITY.md` |

Found on the way: `save_scores` used a numbered SQL parameter `?2` with a positional
sequence. Python 3.12+ warns about this (an error from Python 3.14), and because
`pyproject.toml` turns app DeprecationWarnings into errors, 43 tests failed on Python 3.12 —
the version in CI and the Docker image. Fixed with a plain `?`.

## Steps for the owner (not doable from code)

1. **Laptop host:** switch the start command to `app:create_server_app` — already done in
   `scripts/serve_public.ps1`; restart it once after `git pull`.
2. **Dependencies:** on every machine run
   `pip install --require-hashes --no-deps -r requirements-dev.lock` (server:
   `requirements.lock`).
3. **Backups:** choose the offsite storage (a USB drive or NAS, or a storage provider that
   keeps data in Kazakhstan), then:
   `python scripts/backup_offsite.py --generate-key` → put `BACKUP_KEY` and
   `BACKUP_OFFSITE_DIR` into `.env`, save the key in a password manager with the second
   developer, run `scripts/register_backup_task.ps1`.
4. **Check after deploy:** `curl -sI https://hrscreen.date/login` shows
   `Strict-Transport-Security`.

## Definition of done

- 3 days in a row a new encrypted copy appears in `BACKUP_OFFSITE_DIR` and
  `restore_check.py` prints `OK` (owner, after step 3).
- Over the AI check quota the API is not called and the recruiter sees why.
- The Docker image installs only from `requirements.lock` and contains no pytest.
- `Strict-Transport-Security` on https://hrscreen.date (owner, after step 1).
- Stopping the server mid-batch and starting it again finishes the batch without a click.
- No tunnel id and no `legacy/` in the tree.

Tests: `tests/test_llm_quota.py` (8), `tests/test_backup_offsite.py` (11), new cases in
`tests/test_security.py` (6) and `tests/test_jobs.py` (4). 390 in total, green on Python
3.12 and 3.13.
