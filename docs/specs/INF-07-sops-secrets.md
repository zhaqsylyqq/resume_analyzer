# INF-07 — Secrets through SOPS

## What

| Piece | Purpose |
|---|---|
| `.sops.yaml` | Which age public keys encrypt `deploy/envs/prod|staging/secrets.enc.yaml`; only `data`/`stringData` values are encrypted. The recipients are placeholders until the two key holders run `scripts/secrets.sh keygen` (their private keys never leave their machines) |
| `deploy/secrets/secrets.example.yaml` | Template: Secret `hrscreen-web` and Secret `hrscreen-worker` — the names the Helm chart reads (INF-05), so each workload gets only its own Secret |
| `scripts/secrets.sh` | `keygen`, `encrypt <env>` (plain copy deleted), `edit <env>`, `apply <env>` (decrypt → `kubectl apply`), `check` |
| `scripts/check_secrets.py` | Refuses any `deploy/envs/*/secrets*.yaml` that is a plain copy, has no SOPS/age metadata, or has one value that is not `ENC[...]` (tests: `tests/test_check_secrets.py`) |
| `.gitignore` | `deploy/envs/*/secrets.yaml` (working copies) |
| `docs/runbooks/secrets-rotation.md` | Key holders, first setup, AI-key rotation without downtime, other secrets, a holder leaving |
| `chart.yml` (CI) | Checks the committed files; then the whole path in the kind cluster: template → filled → encrypted with a throw-away age key → checked → decrypted → applied → the chart starts with these Secrets; web reads only `hrscreen-web`, worker only `hrscreen-worker` |

Decryption happens on the admin machine (`secrets.sh apply`) until Argo CD exists; CD-02 adds
the decryption plugin so the cluster applies them itself.

## Known gap

The vacancy bias check still calls the AI from the web process, so `hrscreen-web` still
contains the AI key (marked in the template). Moving that call to the worker removes it
(plan risk R-08 / T-09); then the line goes from the template and the DoD check "web has no
AI key" is added to `chart.yml`.

## Done when (needs people and a cluster)

- Both key holders have generated keys and their public keys are in `.sops.yaml`.
- `secrets.enc.yaml` for staging and prod committed; `scripts/secrets.sh check` green.
- AI-key rotation drill on staging done by the runbook without downtime.

## Verification now

- `scripts/check_secrets.py`: 4 tests (encrypted passes; one plain value, a plain working
  copy, missing metadata, `data:` section — all refused).
- Locally: template → `sops --encrypt` with an age key → check passes → decrypt gives the two
  Secrets; a file with one value replaced by plain text is refused (8 findings).
- gitleaks finds nothing in an encrypted file (so no allow-list is needed).
- The CI kind run (chart.yml) applies Secrets that went through SOPS.
