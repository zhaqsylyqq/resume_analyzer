# APP-03 — Moving the data to PostgreSQL, PostgreSQL backups

## Data move

`flask --app app db-move --to postgresql://… [--from instance/app.db] [--replace]`
(`app/services/datamove.py`), run with the site stopped:

1. brings the SQLite file up to the current columns (as the app does at start) and migrates
   the PostgreSQL schema to the newest revision;
2. refuses a target that already holds data unless `--replace` (which empties it first), so
   two copies can never be mixed;
3. copies all 11 tables in foreign-key order with their original ids, in **one transaction**
   (everything or nothing);
4. moves the identity sequences past the copied ids;
5. compares every table on both sides: row count **and** a SHA-256 over all values in key
   order. Any difference → exit code 1, the site stays on SQLite.

The SQLite file is only read (a test proves its fingerprint is unchanged), so switching back
is removing `DATABASE_URL` from `.env`.

## Backups on PostgreSQL

The same commands work on both engines:

| Command | SQLite | PostgreSQL |
|---|---|---|
| `backup` | file snapshot `app-*.db` | `pg_dump` custom format `app-*.dump` |
| `backup-offsite` / `backup-auto` | encrypted `app-*.db.enc` | encrypted `app-*.pg.enc` (same AES-256-GCM format and key) |
| `restore-check` | integrity check + row counts | `pg_restore --list` (all tables present); with `BACKUP_RESTORE_URL` a full restore into that scratch database + row counts |

- The password never goes on a command line: `pg_dump`/`pg_restore` get it in `PGPASSWORD`
  of the child process only.
- `BACKUP_RESTORE_URL` must differ from `DATABASE_URL` (checked by the configuration); it is wiped.
- Rotation keeps the newest 30 copies of both kinds together; the USB-drive task needs no change.

## CI

- The PostgreSQL job now also creates `hrscreen_restore` and runs the backup and move tests
  with real `pg_dump`/`pg_restore`.
- Coverage: each engine alone covers less (the other engine's branches), so the 90 % floor is
  enforced on the **combined** SQLite + PostgreSQL data in the new job
  "Coverage (both engines)" (locally one engine: 85 %). Measured combined: 94 %.

## Verification

- `tests/test_datamove.py` (7, PostgreSQL): a realistic SQLite database (users with 2FA,
  analysed resumes, scores, candidate status, settings, audit, AI usage) moves with every
  table matching; the app logs in on the moved data and new rows get fresh ids; a second move
  is refused, `--replace` works; the SQLite file is unchanged; the command masks the password;
  PostgreSQL backup encrypted (no `PGDMP` header visible), listed, restored and counted, wrong
  key refused, rotation; `backup`/`backup-offsite`/`restore-check`/`backup-auto` on PostgreSQL;
  the scratch database can never be the live one.
- Both engines: SQLite 501 passed; PostgreSQL 488 passed (cloud).
