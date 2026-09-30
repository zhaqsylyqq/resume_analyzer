# Progress

## Status
| Phase | Scope | State |
|-------|-------|-------|
| 0 | Skeleton | done |
| 1 | F7 Gemini service | next |
| 2 | F4 parsing + F1 batch upload | todo |
| 3 | F3 storage + F6 append | todo |
| 4 | F2 ranking UI + CSV | todo |
| 5 | B1 bias check + B2 diversity | todo |
| 6 | Polish, README, Dockerfile | todo |

## Done
- Phase 0: app factory, env-based config, CSRF + security headers, RU/KK i18n, design system
  (light/dark, responsive), error pages, pytest harness, `scripts/smoke_test.py`.

## Next
- Phase 1: `app/services/gemini.py` — structured extraction + fit scoring, validation,
  retry, backoff on 429/503, fallback model.

## Known issues / notes
- `gemini-2.5-flash` is no longer available to new API keys; default model is `gemini-3.8-flash`
  with `gemini-3.5-flash` as fallback. `gemini-3.8-flash` returns 503 under load at times.
