# Backups and checks never change the database

## Incident, 2026-11-24 20:20 (local time)

While a new branch (SEC-02b) was being put on the laptop, a failed `git checkout` left the
SEC-02b code on the disk for about 25 minutes. The scheduled task `hrscreen-offsite-backup`
(every 30 minutes, also without the USB drive) ran `flask --app app backup-auto` with that code.
Any `flask` command builds the whole app first, and the app's start-up brought the live database
to the code's version: it applied migrations 0004 and 0005, created a data key
(`instance\.data_key`, outside production) and encrypted the personal data with it. Nothing was
lost (checked: `alembic current`, `db-check`, all rows in place), but the live database moved to
code nobody had reviewed, and the only copy of the new key was a file on the laptop.

## Change

`create_app(read_only=…)`. By default the app looks at the `flask` command it runs
(`app.cli_command`, which understands `flask …`, `flask.exe …` and `python[w] -m flask …`); for
the commands in `READ_ONLY_COMMANDS` — `backup`, `backup-offsite`, `backup-auto`,
`restore-check`, `db-check` — it starts read-only:

- no schema migration, no requeue, no encryption pass (`storage.init_db` is skipped);
- no retention purge at start-up;
- no new key file (`instance/.data_key`, `.secret_key` aside): without a configured key the
  keyring stays empty — copies and checks never decrypt anything;
- an absent SQLite file is not created.

The web server, the workers, `db-upgrade` and the admin commands start as before (they
deliberately bring the database to the code's version). `db-check` now also says when the
database and the code disagree: `schema 0003_rate_limits (this code expects
0005_sealed_demographics: …)`.

Other measures (in the team's handbook): before any branch switch on the laptop, check that git
may delete files (a failed checkout is what left the wrong code on the disk); never leave code
with migrations on the disk "for a moment".

## Verification

`tests/test_readonly_commands.py`: the command parser on Windows and POSIX command lines; a
read-only start creates no key file and no database; the incident replayed — plaintext rows
written by older code, then a `backup-auto` start of newer code (`sys.argv` as the Windows
task has it): no migration, no encryption pass, no purge, the rows unchanged, `backup` and
`db-check` still work; `db-check` reports a schema/code mismatch on PostgreSQL.
