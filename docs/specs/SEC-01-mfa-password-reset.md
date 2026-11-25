# SEC-01 — Two-factor sign-in, own password change, password reset

## What users get

- **Profile page** (`/account`, click your name; a person icon on phones):
  - change your own password (asks for the current one);
  - switch on two-factor sign-in: scan a QR code with any authenticator app (Google
    Authenticator, Microsoft Authenticator, Aegis…), type a code to confirm, save the
    **10 one-time recovery codes** shown once;
  - make new recovery codes or switch 2FA off (both ask for the password and a current code).
- **Sign-in with 2FA:** password → second page asks for the 6-digit code or a recovery code.
  The half-signed-in state lasts 5 minutes; wrong codes count toward the same lock-out as
  wrong passwords (5 per 15 min per user and per IP).
- **Forgotten password:** an admin opens Users → "Ссылка для сброса пароля" and passes the link
  on (in person, messenger). The link works once, for 1 hour; a newer link replaces older ones.
  There is no e-mail sending in the system, so self-service reset by e-mail is not offered.
- **Admins** see a "2FA" badge per user and can switch 2FA off for someone who lost the phone
  and the codes. `ADMIN_MFA_REQUIRED=1` sends admins without 2FA to the Profile page until
  they switch it on (recruiters are never forced).
- **Last resort on the server:** `flask --app app reset-2fa USER`,
  `flask --app app reset-link USER [--base-url https://hrscreen.date]`.

## Security details

- TOTP per RFC 6238 (SHA-1, 6 digits, 30 s), standard library only; ±1 step of clock drift;
  each step is accepted once (`users.totp_last_step`), so an observed code cannot be replayed.
- Recovery codes: 10 × ~60 bits, only SHA-256 hashes stored, each usable once.
- Reset links: 256-bit random token, only its SHA-256 is stored (`password_resets`); setting
  the password and burning the link happen in one transaction.
- **A reset link does not switch 2FA off**: a leaked link alone cannot take over a protected
  account.
- The session fingerprint now covers the password hash *and* the 2FA secret: changing the
  password, switching 2FA on/off or a reset signs out every other session of that user.
- The secret being set up is kept in the user's own signed session cookie until confirmed.
- New audit actions: `password_change`, `password_reset`, `mfa_enable`, `mfa_disable`,
  `mfa_recovery_codes`; logins record `(2fa)` / `(recovery code)`.
- Known limit: the TOTP secret is stored in the database unencrypted, like the rest of the
  data today; field encryption is SEC-02b.

## Dependencies

`segno` 1.6.6 (pure Python, no dependencies) draws the QR code as inline SVG; locks updated,
nothing else changed.

## Verification

- `tests/test_mfa.py` (22 tests): RFC 6238 vector, drift and replay, recovery codes, QR,
  enabling (QR, wrong code, codes shown once, only hashes stored), other sessions signed out,
  sign-in needs the code, replayed code refused, recovery code once, 5-minute expiry, rate
  limit, disabling and new codes need password + code, password change, reset link once /
  expired / replaced / unknown, reset keeps 2FA on, admin switches 2FA off, recruiters cannot
  create links, `ADMIN_MFA_REQUIRED`, CLI commands, setting.
- Checked in a browser on desktop and phone widths.
