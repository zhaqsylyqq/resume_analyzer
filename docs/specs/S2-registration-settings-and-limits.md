# S2 — Registration switches, email, daily resume limit

## Goal
Let admins open sign-up to everyone (no invite code) without editing `.env`, collect an
email for every new account, and protect the prepaid AI balance from abuse.

## Settings (admin, page "Пользователи", block "Регистрация")
Stored in a `settings` table, so they apply at once without a restart:
- **Registration open** (checkbox). Off: `/register` answers 404, no link on the login page.
- **Require invite code** (checkbox) and the **code**. When on, the code must not be empty.
- **Daily resume limit per recruiter** (number, 0 = no limit, default 30).
Defaults when nothing is saved yet: registration is open with an invite code if
`INVITE_CODE` is set in the environment (S1 behaviour), otherwise closed; the limit comes
from `DAILY_RESUME_LIMIT` (default 30). Every change is audited (`settings_update`).

## Sign-up
- Fields: username, email, password, password again; plus the code only when required.
- Email is required for self sign-up, checked for a plausible format, stored lower-cased in
  `users.email` (new column, migration). No confirmation email is sent.
- At most 5 accounts per IP per hour (429 with a message); wrong codes stay limited as in S1.
- Admins may give an email when creating a user (optional). Email appears in the users
  table, the statistics page and its Excel report.

## Daily resume limit
- Counts resumes accepted for analysis in the last 24 hours across all sessions the user
  owns (parse failures and duplicates are not counted). Admins have no limit.
- An upload that would exceed it accepts the first files up to the limit and rejects the
  rest with "daily limit reached" (RU/KK). The upload card shows how many resumes the user
  can still add today.
- Re-analysis and new vacancies on resumes already uploaded are not limited.

## Acceptance criteria
- Admin switches change `/register` immediately: closed → 404; open without code → no code
  field; open with code → code required.
- Email required and validated; the 6th sign-up from one IP within an hour is refused.
- A recruiter with limit 3 uploading 5 files gets 3 accepted and 2 rejected; an admin is
  not limited; limit 0 means unlimited.
