# APP-09 — Service commands

## Why

Maintenance lived in separate scripts that guessed the database location (`instance/app.db`)
and read `.env` on their own. After APP-01 the database can live in `DATA_DIR` and secrets
can come from files; the next stages run maintenance as Kubernetes Jobs/CronJobs from the
same image. All service tasks therefore go through one entry point that reads the same
validated configuration as the server.

## Commands (`flask --app app <command>`)

| Command | What it does |
|---|---|
| `backup [--out DIR] [--keep N]` | Consistent SQLite snapshot (online backup API) into `<data>/backups`, checked with `integrity_check`; keeps 14 by default |
| `backup-offsite [--out DIR] [--keep N]` | The same snapshot encrypted with AES-256-GCM into `BACKUP_OFFSITE_DIR`; keeps 30 |
| `restore-check [FILE] [--restore-to PATH]` | Decrypts the newest (or given) copy into a temp file, checks it, prints row counts; optionally restores |
| `db-check` | Integrity check of the live database, row counts, resumes by status, sessions waiting for analysis |
| `generate-backup-key` | Prints a new `BACKUP_KEY` |
| `config-check` | APP-01 configuration report (also `python -m app.check_config`) |
| `purge-old`, `create-admin` | Unchanged (R1, A1) |

Exit code 0 on success, 1 with a one-line reason; ASCII output for Windows logs. In Docker:
`docker compose -f deploy/docker-compose.yml exec app flask --app app db-check`.

## Other changes

- The backup logic moved from `scripts/` into `app/services/backup.py` (no behaviour change,
  same file format `HRSB1`). `scripts/backup_db.py`, `backup_offsite.py`, `restore_check.py`
  are thin wrappers, so the Windows scheduled task `hrscreen-offsite-backup` keeps working
  unchanged; `backup_db.py` now honours `DATA_DIR`.
- `BACKUP_OFFSITE_DIR` and `BACKUP_KEY` are part of the APP-01 configuration: `BACKUP_KEY`
  is validated at start-up (32-byte base64), can come from `BACKUP_KEY_FILE`, and is shown
  only as set / not set.

## Verification

- `tests/test_cli.py` (9 tests): commands registered, backup to the default folder and a
  missing database, offsite backup + restore check + restore, missing folder / key, wrong
  key, `db-check` after a real analysis, key generation, key validation in the configuration.
- Existing backup tests (`tests/test_backup_offsite.py`, retention/backup tests) pass
  unchanged through the wrappers.
- On the laptop: `flask --app app db-check` and `restore-check` against the real database
  and the real copies on the USB drive.
