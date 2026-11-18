# APP-01 — One validated configuration

Stage 2 of the enterprise roadmap starts here: before the app moves to PostgreSQL, a worker
process and Kubernetes, every process must read its settings the same way and refuse to run
with a bad one.

## What changed

- `app/config.py`: one typed, frozen `Settings` object built by `load_settings()` from the
  environment (or `.env`). The Flask config keys are unchanged (`Settings.to_flask()`), so no
  route or service had to change.
- **Fail fast.** A wrong value no longer turns silently into the default. `create_app()`
  raises `ConfigError` with *every* problem listed, e.g.
  `RETENTION_DAYS='ninety' is not a whole number`, `LLM_PROVIDER='openai' must be one of…`,
  `TRUSTED_PROXIES: 'x' is not an IP address or network`. Negative limits are errors now
  (before, `-5` silently became `0`).
- **`APP_ENV`** = `development` (default) | `production` | `test`. Production:
  - the key of the chosen AI provider is required;
  - `SESSION_COOKIE_SECURE` defaults to on and cannot be off;
  - a new session secret is never generated — it must come from `FLASK_SECRET_KEY`,
    `FLASK_SECRET_KEY_FILE` or an existing `instance/.secret_key` (otherwise every restart
    or every replica would log everyone out).
- **Secrets from files:** `FLASK_SECRET_KEY_FILE`, `DEEPSEEK_API_KEY_FILE`,
  `GEMINI_API_KEY_FILE`, `INVITE_CODE_FILE` — the way Docker/Kubernetes mount secrets.
  Setting both `NAME` and `NAME_FILE` is an error.
- `FLASK_SECRET_KEY` shorter than 32 characters and a non-HTTPS `DEEPSEEK_BASE_URL` are rejected.
- **`DATA_DIR`** moves the database and the generated secret out of the code folder
  (a volume in Docker/Kubernetes).
- **`python -m app.check_config`** (also `flask --app app config-check`) prints every setting;
  secrets appear only as `set` / `not set` / `set (from NAME_FILE)`. Exit code 1 with the list
  of problems when something is wrong.
- `scripts/serve_public.ps1` (the public site on the laptop) runs with `APP_ENV=production` and
  checks the configuration before it opens the tunnel; `deploy/docker-compose.yml` sets
  `APP_ENV=production`.

## Verification

- `tests/test_config.py` (18 tests): defaults equal the old behaviour, all problems reported at
  once, booleans, CIDR proxies, secret files (missing, empty, both set), secrets never printed,
  production rules, secret-key generation, `DATA_DIR`, `create_app` refusing a bad config,
  the check command.
- Full suite, ruff, mypy, coverage ≥ 90 % pass.
- The laptop's real `.env` passes `python -m app.check_config` in production mode.
