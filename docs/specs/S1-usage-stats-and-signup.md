# S1 — Usage statistics and sign-up with an invite code

## Goal
The course grade depends on proving that 20–30 people used the service. Admins need one
page with the evidence and a file to hand in, and classmates must be able to create their
own accounts without an admin typing in 30 users.

## Sign-up with an invite code
- `INVITE_CODE` setting (empty by default = sign-up is off). When set, `/register` shows a
  form: invite code, username, password, password again. A correct code creates an active
  `recruiter` and signs them in; the login page links to it.
- Same username and password rules as admin-created users. The code is compared in
  constant time; wrong codes count as failed attempts for the IP in the login limiter
  (5 per 15 minutes), so the code cannot be guessed.
- Audit action `register` (detail: IP). Registration can never create an admin.
- With `INVITE_CODE` empty, `/register` answers 404 and the link is hidden.

## Usage statistics (`/admin/stats`, admins only)
Numbers come from `users`, `audit_log`, `sessions` and `resumes`; no AI call.
- Summary cards: registered users, users who signed in at least once, users who created
  at least one screening session ("active users"), screening sessions created, resumes
  analyzed, exports downloaded. Period: all time, or the last 7 / 30 days (`?days=`).
- Daily table and bar chart for the period: users who signed in, sessions created,
  exports. Inline SVG (no scripts, CSP stays strict).
- Per-user table: username, role, registered, first and last sign-in, number of sign-ins,
  sessions created, resumes analyzed, exports.
- Sessions and resumes are counted from the audit log where possible (`session_create`),
  so sessions later deleted by retention still count; resumes analyzed counts what is
  still stored.
- "Download Excel": sheets Summary, Daily, Users and Audit log (every entry in the period),
  for the instructor. Formula-safe like the other exports.
- RU and KK.

## Acceptance criteria
- With `INVITE_CODE` set, a visitor with the right code gets a recruiter account and is
  signed in; a wrong code fails and is rate-limited; without the setting `/register` is 404.
- The stats page and the Excel file count users, sign-ins, sessions, resumes and exports
  correctly for all time and for a period (tests with fixed audit rows).
- Recruiters get 404 on `/admin/stats` and its export.
