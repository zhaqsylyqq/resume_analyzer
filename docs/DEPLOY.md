# Deployment: hrscreen.date

The app is live at **https://hrscreen.date**. It runs on the owner's laptop
(`scripts/serve_public.ps1`) through a Cloudflare Tunnel. The goal is to move it to an
always-on server so the laptop can be switched off. Domain, tunnel, AI key and sign-in
stay the same.

## What already exists

| Piece | State |
|---|---|
| Domain | `hrscreen.date`, registered at Cloudflare Registrar in the owner's Cloudflare account; DNS on Cloudflare |
| Tunnel | Named tunnel `hrscreen` (id `c256e54f-7d99-466c-a77b-27e0f52fcc53`); the DNS record `hrscreen.date` is a CNAME to it |
| AI | DeepSeek (`LLM_PROVIDER=deepseek`, `deepseek-flash`), prepaid balance of about $5; about $0.001 per resume |
| Sign-in | Personal accounts with roles (A1, `app/auth.py`). Admins open registration with an invite code on the users page (see [`TEAMMATE.md`](../TEAMMATE.md)) |
| Cloudflare Access | Not used (the app's own login covers it) |
| Laptop runner | `scripts/serve_public.ps1`: waitress on 127.0.0.1:8000 plus `cloudflared tunnel run` |

## Target setup

A small always-on Linux server runs [`deploy/docker-compose.yml`](../deploy/docker-compose.yml)
with two containers:

- `app`: the existing `Dockerfile` (waitress, non-root). SQLite lives in the `app-data`
  volume, so data survives restarts and rebuilds.
- `tunnel`: `cloudflared` connects the same tunnel `hrscreen` with a tunnel token. No
  inbound ports are opened and no web server or certificates are needed; Cloudflare
  terminates HTTPS.

**Server.** Any small x86 VPS with 2 GB of RAM or more and Docker is enough; the app is one
process with SQLite. Expect a few euros per month at providers like Hetzner, DigitalOcean
or Contabo. Check current prices: some bill hourly, so a short demo costs cents. Do not use
free hosts with an ephemeral disk (for example Render free or Hugging Face Spaces without
persistent storage): SQLite would be wiped on every restart or sleep.

## Secrets the owner hands over (never via git or chat logs)

Send these through a password manager or another private channel. On the server they go
into `/opt/hrscreen/.env` (or wherever the repo is cloned), file mode `600`.

| Variable | Where it comes from |
|---|---|
| `LLM_PROVIDER=deepseek` | fixed value |
| `DEEPSEEK_API_KEY` | DeepSeek platform. Best: a new key named "server", so it can be revoked separately |
| `TUNNEL_TOKEN` | the owner runs `& "$env:USERPROFILE\cloudflared\cloudflared.exe" tunnel token hrscreen` in PowerShell on the laptop (it prints the token) |
| `INVITE_CODE` (optional) | only if registration should open with a code before an admin saves the settings |

Optional: the owner invites the teammate to the Cloudflare account (Manage account →
Members) to manage DNS and the tunnel directly.

## Steps for the agent on the server

1. Install Docker Engine and the compose plugin. Clone the repository.
2. Create `.env` in the repo root with the variables above (`chmod 600 .env`).
3. Build and start:
   `docker compose -f deploy/docker-compose.yml --env-file .env up -d --build`.
4. Check `docker compose -f deploy/docker-compose.yml logs -f tunnel` for
   `Registered tunnel connection`, then `curl -sI https://hrscreen.date/` (expect a 302
   to `/login`).
5. Create the first admin in the server's database:
   `docker compose -f deploy/docker-compose.yml exec app flask --app app create-admin`.
   Then sign in and open registration on the users page ([`TEAMMATE.md`](../TEAMMATE.md),
   step 5). The laptop's database is not moved; the server starts empty. If the owner
   wants the old sessions, copy a `scripts/backup_db.py` snapshot into the `app-data`
   volume before the first start.
6. **Cutover:** the owner stops the laptop runner (Ctrl+C in its window) as soon as the
   server connector is registered. Two connectors on one tunnel would split traffic
   between two different databases.
7. Sign in on https://hrscreen.date, screen a few sample resumes, then
   `docker compose -f deploy/docker-compose.yml restart` and confirm the sessions are
   still there.
8. Schedule `scripts/backup_db.py` daily inside the app container (README, "Data
   retention and backups").

## Done when

- https://hrscreen.date answers with the laptop off.
- Without signing in, only `/login`, `/register` (when open) and `/healthz` are reachable.
- A batch of the sample resumes completes.
- Data survives `docker compose restart` and a server reboot.
- `PROGRESS.md` records the server, its cost and how to reach the logs.

## Caveats (unverified)

- `deploy/docker-compose.yml` has not been run yet. The Docker image itself was built and
  checked by the teammate (PROGRESS phase 14).
- Running a CLI-created tunnel with `TUNNEL_TOKEN` and `--url` is the documented
  cloudflared behaviour, but it has not been tested on this tunnel. If it fails, create a
  dashboard-managed tunnel (Zero Trust → Networks → Tunnels), add the public hostname
  `hrscreen.date` → `http://app:8000`, and use its token.
- The image tag `cloudflare/cloudflared:2026.10.7` matches the version used on the laptop.
  Confirm that the tag exists on Docker Hub.
