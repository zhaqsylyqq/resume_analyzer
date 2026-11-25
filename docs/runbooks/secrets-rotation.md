# Runbook: secrets of the cluster (SOPS + age) — INF-07

Secrets of each environment live in Git **encrypted**: `deploy/envs/<env>/secrets.enc.yaml`
(two Kubernetes Secrets, `hrscreen-web` and `hrscreen-worker`). Only the `stringData` values
are encrypted (AES-256-GCM by SOPS); names stay readable in reviews. The data key of each file
is encrypted for every **age** public key listed in `.sops.yaml`. Whoever has one of the
matching private keys can decrypt; nobody else can, GitHub and CI included.

Tools: `sops` and `age` (Windows: `winget install FiloSottile.age` and `winget install
Mozilla.SOPS`, or the release binaries), `kubectl` for `apply`.

## Key holders (two people, decided 2026-11-13: Abzal and the teammate)

1. Each holder once: `scripts/secrets.sh keygen` — creates `~/.config/sops/age/keys.txt`
   (Windows: `%AppData%\sops\age\keys.txt`) and prints the **public** key `age1…`.
2. Keep an offline copy of the private key (a second USB stick or paper, not the backup stick).
   **Losing both private keys = the secrets file cannot be opened**; the values then have to be
   re-created (new FLASK_SECRET_KEY signs everyone out; DATA_KEY can only be recovered from
   `instance\.data_key` / its copies, so keep those separately too).
3. Put both public keys into `.sops.yaml` (prod and staging rules; separate keys per
   environment are fine too), commit.

## First time for an environment

```
cp deploy/secrets/secrets.example.yaml deploy/envs/prod/secrets.yaml   # git-ignored
# fill in the real values (DATA_KEY must be the key the data is encrypted with: id 69c71642 today)
scripts/secrets.sh encrypt prod        # writes secrets.enc.yaml, deletes the plain copy
scripts/secrets.sh check               # what CI runs: every value is ENC[...]
git add deploy/envs/prod/secrets.enc.yaml && git commit
scripts/secrets.sh apply prod          # on the cluster admin machine: decrypt + kubectl apply
```

## Rotate the AI key without downtime (DoD drill: do it once on staging)

1. Create the new key at the provider; keep the old one valid.
2. `scripts/secrets.sh edit staging` → replace `DEEPSEEK_API_KEY` in both Secrets → save.
3. `scripts/secrets.sh apply staging`
4. `kubectl -n hrscreen-staging rollout restart deploy/hrscreen-web deploy/hrscreen-worker`
   — pods are replaced one by one (`maxUnavailable: 0`), workers finish resumes in hand.
5. Check `/metrics`: `hrscreen_llm_requests_total{outcome="ok"}` keeps growing, no `401`.
6. Revoke the old key at the provider. Commit the edited `secrets.enc.yaml`. Note it in the work log.

## Other secrets

| Secret | How to rotate | Effect |
|---|---|---|
| `FLASK_SECRET_KEY` | new `openssl rand -hex 32` in both Secrets, apply, restart | everyone is signed out once |
| `DATA_KEY` | put the old value into `DATA_KEY_OLD`, a new key into `DATA_KEY` (both Secrets), apply, restart; `flask --app app data-key-rotate` (a Job) re-encrypts; then remove `DATA_KEY_OLD` | none, if done in this order (SEC-02b) |
| `DATABASE_URL` password | `ALTER ROLE … PASSWORD` in PostgreSQL, then the same steps as the AI key | none (pods restart one by one) |
| age key of a holder | remove/replace the public key in `.sops.yaml`, then `sops updatekeys deploy/envs/<env>/secrets.enc.yaml` for each file; if the key leaked, rotate every value above too | — |

## A holder leaves or a laptop is lost

Remove their public key (`sops updatekeys`), and because they could read every value,
rotate the AI key, `FLASK_SECRET_KEY`, the database password and `DATA_KEY` as above.
