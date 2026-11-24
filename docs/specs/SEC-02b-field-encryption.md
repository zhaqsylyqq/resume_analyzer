# SEC-02b — Personal data encrypted in the database

## Why

Resumes are personal data (Law of the Republic of Kazakhstan «On personal data and their
protection»). Until now a stolen database file, a raw `pg_dump` (the local `backup` command
writes one to `instance/backups`), a database administrator or an SQL injection could read
every resume, name, phone and e-mail. Threats R-03, T-07, T-08 of the roadmap.

## What is encrypted

| Column | What it holds |
|---|---|
| `resumes.text` | the full resume text |
| `resumes.filename` | the uploaded file name (often the candidate's name) |
| `resumes.profile_json` | the extracted profile: name, contacts, skills, experience |
| `resumes.raw_file` | the uploaded file while it waits for a worker (APP-05) |
| `demographics.gender`, `demographics.age` | the aggregate-only demographics (B2) |
| `users.totp_secret` | the 2FA secret (whoever has it can generate codes) |

Not encrypted: scores and AI summaries (made from the anonymised view, no names or contacts),
vacancy texts, session titles, user names and e-mails of recruiters (needed for sign-in and the
unique e-mail check), the audit log (append-only, SEC-03; it holds IP addresses and titles,
not resume data). `resumes.sha256` stays: duplicates are still found by content.

## How

`app/services/fieldcrypt.py`: AES-256-GCM (the `cryptography` package, already used for the
backups). A stored value is `enc1:<key id>:<base64url(nonce | ciphertext | tag)>`; the
uploaded file is `ENC1<key id><nonce><ciphertext|tag>`.

- The column name (`resumes.text`, …) is the associated data: a value copied into another
  column does not decrypt.
- A fresh 12-byte nonce per value: the same text is stored differently every time.
- The key id is the first 8 hex digits of SHA-256(key), so several keys can be configured at
  once and every value says which one sealed it.
- Values written before SEC-02b (no prefix) are read as they are.

`app/services/storage.py` seals on write (`add_resume`, `save_parsed`, `save_profile`,
`update_user(totp_secret=…)`) and opens on read (`get_resume`, `list_resumes`, `load_profile`,
`raw_file`, `totp_secret(user)`); the two queries that join these columns open them too
(`scoring.ranking`, `diversity._load`). The ranking search (`?q=`) already filtered in Python,
so it keeps working. The session fingerprint uses the decrypted 2FA secret: nobody is signed
out by the encryption or by a key rotation.

PostgreSQL migration `0005_sealed_demographics` makes `demographics.age` TEXT (it holds the
sealed value). Migrations never see the key.

### Existing data, rotation, rollback

`storage.seal_all` runs at every start (and as `flask --app app data-key-rotate`): every value
that is plaintext or sealed with an older key is re-encrypted with the current key, row by
row, replacing a value only if it is still the one that was read (safe with several replicas).
Nothing left to do → nothing written.

- **Rotation:** new key in `DATA_KEY` (`python -m app.data_key generate`), the old one in
  `DATA_KEY_OLD`, start the app → everything moves to the new key → remove `DATA_KEY_OLD`.
  Starting with only the new key while data is under the old one stops at start-up with
  "data key xxxxxxxx is not configured" (nothing is damaged).
- **Rollback** to a version before SEC-02b: stop the site, `flask --app app data-key-rotate
  --to-plaintext` (asks first), `alembic downgrade 0004_audit_append_only`, then the old code.

## The key

| Where | When |
|---|---|
| `DATA_KEY` or `DATA_KEY_FILE` | production servers (Docker/Kubernetes secret) |
| `<data folder>/.data_key` | otherwise; outside production it is created on first start |

In production a missing key is an error at start-up ("DATA_KEY … is required in production"),
like `FLASK_SECRET_KEY`: replicas and restarts must never invent a second key.
`python -m app.data_key` works without starting the app: `init` (create the key file once,
never replaces one, never prints the key), `status` (where the key comes from and its id),
`generate` (print a new key). `config-check` shows `DATA_KEY` only as set / not set.

**Losing the key = losing the encrypted data.** The key goes into the password manager shared by
two people (like `BACKUP_KEY`), never onto the backup drive: a backup holds only ciphertext and
needs both keys to be read. Docker compose: `DATA_KEY` in `.env` (app and worker share it).

## Switching the laptop (with the owner)

1. Insert the USB drive; `flask --app app backup-offsite` and `restore-check` (a copy before the change).
2. `.\.venv\Scripts\python.exe -m app.data_key init` → `instance\.data_key`; copy its line into
   the password manager now. `python -m app.data_key status` shows the key id.
3. Start the site: migrations 0004/0005 and the encryption of the existing 68 resumes run by
   themselves (a log line says how many values were encrypted).
4. Check a session, a ranking, an export, sign-in with 2FA. New offsite copy and `restore-check`.
5. Old local dumps in `instance\backups\app-*.dump` (made before the switch) still hold
   plaintext: delete them once the new copy is checked.

## Verification

- `tests/test_fieldcrypt_sec02b.py` (15): round trip, fresh nonce, never sealed twice, column
  binding, unknown key; configuration errors (bad base64, wrong length, old key without a
  new one); where the key comes from (production refuses to invent one, development creates it
  once); `python -m app.data_key` init/status/generate never print the key; after a real upload
  and analysis every personal column starts with `enc1:` and holds no name, e-mail, phone or
  text; the **SQLite file** and a **PostgreSQL `pg_dump`** hold none of them; the ranking,
  session page, CSV/Excel exports and the name search show the decrypted values; the waiting
  upload is sealed; old plaintext rows are encrypted at the next start and only once; rotation
  to a new key (refused without the old key); `data-key-rotate`; rollback to plaintext and back;
  2FA sign-in and the stored secret after a restart.
- Existing tests adjusted where they read raw columns (they now decrypt).
- Whole suite: SQLite 569, PostgreSQL 562; combined coverage 94 %.
