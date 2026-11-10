# A1 — Login, roles and audit log

## Goal
Resumes contain personal data. Only signed-in staff may use the app, and a recruiter sees
only their own screening sessions.

## Behaviour
- **Login** at `/login` with username and password (CSRF-protected form). Passwords are
  hashed with Argon2id (`argon2-cffi`). Usernames are case-insensitive, 3–64 characters of
  letters, digits, `.`, `_`, `-`; passwords are at least 10 characters.
- **Every route requires login** except `/login`, `/healthz`, `/lang/<code>` and static
  files. Pages redirect to `/login?next=<path>` (only same-site paths are honoured); JSON
  requests get `401`.
- **Logout** is a POST (`/logout`, CSRF). The Flask session is cleared on login and on
  logout (no session fixation); it lasts 12 hours.
- **Roles**:
  - `recruiter` — creates sessions and sees only sessions where `owner_id` is their id;
  - `admin` — sees every session (including sessions created before login existed, which
    have no owner) and manages users at `/admin/users`.
  A session the user may not see answers `404` on every route: page, status, upload,
  analyze, add vacancy, ranking, bias re-check, exports, delete.
- **User management** (admin only): create a user (username, password, role), activate or
  deactivate, set a new password. An admin cannot deactivate themselves or remove their own
  admin role. A deactivated user, or one whose password was changed, is signed out on their
  next request (the session stores a fingerprint of the password hash).
- **First admin** comes from the CLI: `flask --app app create-admin` (asks for username and
  password, hidden input with confirmation). There is no default password.
- **Failed logins** are rate-limited per username and per IP: after 5 failures within 15
  minutes further attempts are refused for 15 minutes with `429` and a localized message.
  The error message never says whether the username exists. The limiter lives in memory
  (one process per database, see AGENTS.md).
- **Audit log** (`audit_log` table): time, user, action, session id and a short detail.
  Actions: `login`, `login_failed`, `logout`, `session_create`, `session_open`,
  `ranking_open`, `export` (detail: format and vacancy id), `session_delete` (detail:
  title), `user_create`, `user_update`. Admins see the latest 200 entries at
  `/admin/audit`. Rows survive session deletion. No resume text, passwords or keys.
- **Migration**: on start, `users` and `audit_log` are created and `sessions.owner_id` is
  added if missing. Existing sessions keep `owner_id = NULL` (admin only).
- UI: user name and role in the top bar, logout button, "Users" and "Audit" links for
  admins; all texts in RU and KK.

## Acceptance criteria
- Every route except the public ones redirects or answers 401 without login.
- Recruiter A cannot read, poll, upload to, export or delete recruiter B's session; an
  admin can.
- Wrong password and unknown user give the same message; the 6th failure in a row is
  refused with 429.
- `create-admin` creates an admin with a hashed password and refuses short passwords and
  duplicate names.
- Opening, exporting and deleting a session write audit rows.
