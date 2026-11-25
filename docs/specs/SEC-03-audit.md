# SEC-03 — Append-only audit log

## Why

The audit log (`audit_log`) records sign-ins, session work, exports and admin actions. Until
now nothing stopped a row from being changed or deleted: by an app bug, an SQL injection, an
admin with database access, or someone covering their tracks (threat T-13). Several security
events were missing, and the admin page showed only the last 200 rows.

## What changed

### The log cannot be changed

| Engine | Guard |
|---|---|
| PostgreSQL | migration `0004_audit_append_only`: triggers refuse `UPDATE`, `DELETE` (row) and `TRUNCATE` (statement, also through `TRUNCATE … CASCADE`) for every role, owner and superusers included; `UPDATE/DELETE/TRUNCATE` revoked from `PUBLIC` |
| SQLite | triggers `audit_log_no_update`, `audit_log_no_delete` (created at start-up, `storage.AUDIT_GUARD`) |

The error text is `audit_log is append-only (SEC-03): … is not allowed`. Inserting and
reading work as before. Users are never deleted (only switched off), so the
`user_id … ON DELETE SET NULL` link never has to change a row.

Only the table owner can lift the guard (`ALTER TABLE audit_log DISABLE TRIGGER …`), and only
two places do it, inside one transaction: `db-move --replace` (empties a target database) and
the test suite (empties the test database between tests).

### Least privileges for the app role (PostgreSQL, optional now, needed in Kubernetes)

The triggers stop everybody, but the table owner could still switch them off. The full
protection is for the app to sign in as an **ordinary role that does not own the tables**:

```
flask --app app db-app-role hrscreen_app      # run with the owner's DATABASE_URL, or --owner-url
```

grants that role `SELECT/INSERT/UPDATE/DELETE` on all tables, but on `audit_log` only `SELECT`
and `INSERT`, on `alembic_version` only `SELECT`, plus the sequences; tables of later
migrations get the same rights by default. It refuses a role that does not exist, a
superuser, and the owner itself. The app then starts and works as that role (migrations
already at the newest revision only need reading); new migrations are applied by the owner
(`db-upgrade` with the owner's URL) — in Kubernetes a separate migration job (INF stage).

As that role: `DELETE/UPDATE/TRUNCATE audit_log` → *permission denied*; `ALTER TABLE …
DISABLE TRIGGER`, `DROP TABLE` → *must be owner*.

**Not switched on for the laptop yet.** Today the laptop's app role `hrscreen` owns the
tables and the database. Switching needs the `postgres` admin password, a new owner role and a
short stop of the site; see "Switching the laptop" below. Until then the triggers protect the
log against the app and its users; only someone with the owner's password can lift them.

### Every event is also a structured log line

`storage.audit()` writes the row and then one JSON line on the `hrscreen.audit` logger:

```
audit {"event": "audit", "id": 812, "created_at": "2026-11-24T13:05:11+00:00", "user_id": 3,
       "username": "aru", "action": "mfa_failed", "session_id": null, "detail": "disable: wrong code"}
```

so the events also reach the central logs (Loki, OBS-02) and can be compared with the table.

### New events

| Action | When |
|---|---|
| `access_denied` | a signed-in user opens an admin page or someone else's session (still answered 404; once per 10 min per user and request) |
| `mfa_failed` | wrong code or password when switching 2FA on/off or making new recovery codes |
| `mfa_recovery_used` | sign-in with a recovery code (detail: how many are left) |
| `mfa_reset` | 2FA switched off by an admin or from the command line (was `user_update`) |
| `password_change_failed` | wrong current password on the account page |
| `password_reset_link` | a reset link created by an admin or `reset-link` (was `user_update`) |
| `password_reset_failed` | unknown/expired link (once per 10 min per address), switched-off account, link already used |

Wrong 2FA codes at sign-in stay `login_failed`. All labels exist in RU and KK.

### Admin page `/admin/audit`

The whole log, newest first, 50 rows a page ("← Новее / Старее →"), with filters: action,
user (whole name, any letter case), from/to date (local dates, `STATS_UTC_OFFSET`). Unknown
filter values are ignored. Security events are shown in red.

## Switching the laptop (later, with the owner; outside working hours)

1. Stop the site. As `postgres`, in the `hrscreen` database: `CREATE ROLE hrscreen_owner LOGIN PASSWORD '…'`;
   `REASSIGN OWNED BY hrscreen TO hrscreen_owner` (tables, sequences, the trigger function and
   the databases `hrscreen` owns); then give the scratch databases back, since `restore-check`
   and the tests rebuild them as `hrscreen`: `ALTER DATABASE hrscreen_restore OWNER TO hrscreen`,
   `ALTER DATABASE hrscreen_test OWNER TO hrscreen`.
2. `flask --app app db-app-role hrscreen --owner-url postgresql://hrscreen_owner:…@localhost:5432/hrscreen`.
3. Keep `DATABASE_URL` as is (the app keeps signing in as `hrscreen`); store the owner's password
   like `POSTGRES_ADMIN_PASSWORD` (password manager, not next to the backups).
4. Start the site, `db-check`, sign in, check `/admin/audit`; a backup and `restore-check`.

## Verification

- `tests/test_audit_sec03.py` (22; 4 need PostgreSQL): `DELETE`/`UPDATE` refused on both
  engines and the rows stay; `TRUNCATE` and `TRUNCATE users CASCADE` refused on PostgreSQL;
  the SQLite guard survives a restart; the JSON log line equals the stored row; every new
  event from real requests (recruiter on an admin page, foreign session, wrong passwords and
  codes, recovery code, reset links, admin and CLI 2FA reset), with the 10-minute de-dup;
  pages of 50 and filters (dates across the UTC+5 midnight, letter case, bad values ignored,
  filters kept between pages); a limited role can insert but gets *permission denied* /
  *must be owner* for every way of changing the log, and the whole app runs as that role.
- Whole suite: SQLite 555 passed, PostgreSQL 548 passed; combined coverage 94 %.
- The page checked in a browser at desktop and phone widths (filters, red security events, pages).
