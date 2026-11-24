# Security policy

HR Resume Analyzer processes resumes, which are personal data. Please report security
problems privately, not in a public issue.

## How to report

Open a private report on GitHub: **Security → Report a vulnerability** in this repository.
Include what you found, how to reproduce it and what an attacker could do with it. Do not
access, change or download other people's data while testing; a test account and the
synthetic resumes in `samples/` are enough to show a problem.

We answer within 7 days and tell you when the fix is released.

## Scope

- The application code in this repository and its deployment files.
- The public site hrscreen.date: only with your own test account, no load or denial of
  service tests, no automated scanning without asking first.

Out of scope: the AI providers (DeepSeek, Google Gemini) and Cloudflare themselves — report
those to the vendors.

## What we already do

Passwords are hashed with Argon2id; every form is protected against CSRF; a strict Content
Security Policy is sent; recruiters can only open their own screening sessions; scoring sees
an anonymized profile without gender, age, family status or contacts. Resumes that try to
instruct the AI are flagged for the recruiter. The threats we consider and how each is handled
are in [`docs/security/threat-model.md`](docs/security/threat-model.md); an OWASP ZAP baseline
scan of a throw-away copy runs in CI every week. The roadmap of further work is tracked by task
IDs (QW, CI, APP, SEC, INF, CD, OBS, PRD).
