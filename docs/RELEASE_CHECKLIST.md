# Release checklist (hrscreen.date)

Agreed at the SIS 13 retrospective on 24.11.2026 after two problems that users found before
we did (sign-up blocked behind the tunnel, broken phone layout) and the 24.11 backup incident.
Run it for every release to the live site. Write the result in the work log.

## Before switching code on the server

1. All CI checks of the PR are green and the PR is merged (see the PR template).
2. Git on the server machine works without errors: a test file in `.git` can be created **and
   deleted**. If deletion is refused, stop and fix permissions first (lesson of 24.11).
3. `flask --app app db-check` shows the expected schema and record counts.
4. A fresh backup exists: `backup` (and `backup-offsite` when the USB drive is in), then
   `restore-check` passes.
4a. Servers that run the container image (Docker, cluster): run only the digest published by
    `build.yml` (its run summary shows it) and check it first with
    `scripts/verify_image.sh ghcr.io/zhaqsylyqq/resume_analyzer@sha256:...` (CD-01).

## After the switch, through the public address (Cloudflare Tunnel)

5. `https://hrscreen.date/healthz` answers `{"status": "ok"}`.
6. Sign in as a test recruiter **on a phone** (or 390 px browser width): no horizontal scroll.
7. Sign up a test account from a second device or network: it is not blocked.
8. Upload one sample resume, wait for the score, open the ranking, export Excel.
9. `db-check` again: counts grew only by the test data.
10. Note the release in the work log: date, branch, commit, who checked, problems found.

If any step fails: switch back to the previous commit and restore the backup from step 4.
