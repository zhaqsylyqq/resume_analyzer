# Spec 00 — Application skeleton

## Goal
A runnable Flask app with configuration, security baseline, RU/KK layout and a test harness,
so every later feature plugs into the same structure.

## Scope
- `create_app(overrides)` factory; settings read from the environment after `load_dotenv()`.
- `.env.example` documents every variable; `.env` is git-ignored.
- Flask secret: `FLASK_SECRET_KEY`, otherwise a random key persisted in `instance/.secret_key`.
- Security baseline:
  - CSRF token per session, required on every non-GET request (form field `csrf_token`
    or header `X-CSRF-Token`), compared in constant time.
  - Strict CSP (`'self'` only, no inline scripts or styles), `nosniff`, `DENY` framing,
    `same-origin` referrer, restrictive `Permissions-Policy`, HSTS when served over HTTPS.
  - Session cookie `HttpOnly`, `SameSite=Lax`, `Secure` when `SESSION_COOKIE_SECURE=1`.
  - Redirect targets accepted only as same-site relative paths.
- i18n: dictionary-based `t(key)`; language from the `lang` cookie (`ru` default, `kk`).
  RU and KK must define identical key sets.
- Error pages for 400/403/404/413/500, JSON errors for JSON clients; no stack traces leak.
- UI: design tokens, light/dark theme (system default + persisted toggle), responsive layout,
  skip link, visible focus rings, reduced-motion support.
- `scripts/smoke_test.py`: one real structured-JSON call; `--list-models` lists usable models.

## Out of scope
Features F1–F7, B1, B2.

## Acceptance
- `flask --app app run` serves the home page.
- `pytest -q` passes: rendering, language switch, open-redirect guard, CSRF, headers, errors.
