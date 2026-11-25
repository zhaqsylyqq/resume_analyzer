## What and why

Task ID (roadmap or SIS): 
Spec: `docs/specs/...`

## Checklist before review

- [ ] Tests pass locally (SQLite) and in CI (Tests on PostgreSQL, Coverage).
- [ ] `PROGRESS.md` row and the spec in `docs/specs/` are updated.
- [ ] No secrets, keys, `.env` or database files in the diff.
- [ ] Database changes: an Alembic migration, and backup/check commands still start read-only.
- [ ] If this PR is deployed: `docs/RELEASE_CHECKLIST.md` will be followed.

## Merge rule (agreed at the SIS 13 retrospective, 24.11.2026)

- The reviewer who approves also merges, within 24 hours of the approval.
- Use **Merge pull request → Create a merge commit**. Never "Close" an approved PR.
- Merge open PRs in order (oldest first). After merging, delete the branch on GitHub.
