# APP-10 — Module boundaries and one table of access rules

## Why

`app/auth.py` had grown to 800 lines mixing password hashing, rate limits, sign-in pages,
admin pages and CLI commands. Access to a route was decided in three places: a login check
with a list of public endpoints, an `admin_required` decorator, and `load_session_or_404`
inside each session view. A new route was open to every signed-in user unless its author
remembered the right decorator or call (risks R-20, T-03). The AI layer was still called
"Gemini" although the app runs on DeepSeek.

## Changes (behaviour unchanged)

### `app/policy.py`: who may open which route

One table, `RULES`, gives every endpoint exactly one rule: `PUBLIC` (anyone), `SIGNED_IN`,
`OWNER` (a signed-in user who may see the screening session in the URL: its owner or an
admin), `ADMIN`. `policy.enforce` runs before every request, after the user is loaded:

- anonymous on a non-public route → the sign-in page (401 JSON for scripts), as before;
- recruiter on an admin route, or on someone else's session → 404 and an `access_denied`
  audit record (SEC-03), as before;
- **a route missing from the table → 404 for everyone and an error in the log**: a new route
  cannot go live without a decision about who may open it.

The views keep `admin_required` and `load_session_or_404` as a second line. `is_admin`,
`can_access`, `owner_filter`, `deny` live here now. Routes registered elsewhere (tests,
extensions) get a rule with `policy.declare(endpoint, rule)`.

### `app/auth/` package (was `app/auth.py`)

| Module | Contents |
|---|---|
| `passwords.py` | Argon2id hashing and verification, `needs_rehash`, the session fingerprint, validation of user names, e-mails, passwords |
| `limits.py` | `LoginLimiter`, `SignupLimiter`, `client_ip` (visitor IP behind the tunnel) |
| `login.py` | `/login`, `/login/verify` (2FA), `/register`, `/reset/<token>`, `/logout`, reset links |
| `admin.py` | `/admin/users`, `/admin/settings`, `/admin/users/<id>`, `/admin/stats(.xlsx)`, `/admin/audit` |
| `commands.py` | `create-admin`, `reset-2fa`, `reset-link` |
| `__init__.py` | loading `g.user`, the admin-2FA requirement, `init_auth`; re-exports every old name |

`current_settings` moved to `app/settings.py` (`settings.current`). Blueprint and endpoint
names are unchanged (`auth.login`, `auth.users`, …), so templates and links are untouched.

### AI layer renamed

`app/services/gemini.py` → `app/services/llm.py`, `GeminiService` → `LLMService`,
`get_gemini` → `get_llm`, setting `GEMINI_CONCURRENCY` → `LLM_CONCURRENCY`. Compatibility:
`app/services/gemini.py` re-exports everything with the old names, `GeminiService` and
`get_gemini` are aliases, the old setting is read when the new one is unset (the laptop's
`.env` needs no change), and both config keys carry the value. The provider classes
(`GeminiBackend`, `GeminiError`, …) keep their names: they describe the Gemini backend or are
caught by name across the code.

## Verification

- Routes before and after: the same 41 endpoints, methods and URLs (compared).
- `tests/test_access_matrix.py`: every route has exactly one rule and no rule names a missing
  route; the public routes are an explicit list (making a route public needs a change here
  too); a route without a rule is closed even for an admin and logged; **the matrix**: every
  route × anonymous / owner / other recruiter / admin, on fresh data for each check, against
  its rule (anonymous asked to sign in, refusals are 404 and recorded, allowed visitors are
  never refused). Changing a rule in the table (admin page → signed-in, session page →
  signed-in, an export → signed-in, the start page → public, the account page → admin) makes
  the matrix fail — checked.
- `tests/test_app10_names.py`: old module, class and setting names keep working.
- Whole suite unchanged apart from where tests patched moved internals (the password hasher
  now in `app.auth.passwords`; test routes declare their rule).
