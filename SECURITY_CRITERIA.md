# Security Criteria for Future Add-ons

Use this checklist before adding a route, admin action, background tool, import/export flow, or frontend feature. These criteria summarize the core rules from the 2026-05-25 security review.

## Request Security

- State-changing routes must require server-issued CSRF tokens. Never initialize a CSRF token from a client POST.
- Login, registration auto-login, logout, and privilege changes must clear or rotate session state and CSRF tokens.
- Admin routes and JSON endpoints must check authentication and role authorization before reading or mutating data.
- New rate-limited routes must use the shared limiter. Hosted production must use Redis-backed rate limiting, not process memory.
- Code running behind Render or another reverse proxy must keep `ProxyFix` enabled and configured for the trusted proxy depth.

## Data and Output Safety

- Treat all database strings, CSV import data, public report text, and admin-entered labels as untrusted.
- Use `textContent`, `replaceChildren`, and DOM node creation for frontend rendering. Do not use `innerHTML` with untrusted values.
- CSV exports intended for humans must pass every cell through `csv_safe_row` or `csv_safe_cell`.
- SQL queries must use SQLAlchemy expressions or bound parameters. If an identifier must be dynamic, validate it against a small allowlist before interpolation.
- URL fields must be normalized and allowlisted by purpose. Book covers must stay restricted to trusted cover hosts.

## Secrets and Bootstrap

- Hosted production must fail closed without `FLASK_SECRET_KEY` or `APP_SECRET_KEY`, `INVITE_CODE_PEPPER`, and `REDIS_URL`.
- Plaintext `ADMIN_PASSWORD` is development-only. Production should use precomputed `ADMIN_PASSWORD_HASH` only.
- Do not auto-create or auto-promote production admins during web startup. Use invite-code tooling for normal onboarding.
- `EXIS_ENABLE_ADMIN_BOOTSTRAP=1` is an emergency one-time bootstrap escape hatch and should not remain enabled.
- Never log raw database URLs, passwords, invite codes after creation, API keys, or session/CSRF secrets.

## Dependencies and CI

- `requirements.txt` is the production runtime contract. Keep it small and pinned.
- Put test/security tools in `requirements-dev.txt`; put optional import/scraping helpers in `requirements-tools.txt`.
- Do not add `psycopg2-binary` to production runtime dependencies.
- CI must install the same runtime dependency set that production uses, then run tests, `pip-audit`, Bandit, and a frontend DOM-sink guard.
- Dependabot security updates should stay enabled for pip dependencies.

## Backup and Operations

- Do not commit database backups, CSV exports, local SQLite files, or other operational data snapshots. Ignore rules do not protect files that are already tracked.
- In-app `BackupArchive` records are convenience snapshots, not disaster recovery backups.
- Durable recovery must use Render Postgres PITR/logical backups or an external object-store dump job.
- Object-store dump jobs must create a PostgreSQL logical dump, validate it before upload, and verify the remote object after upload. The bucket must be independent, versioned, encrypted, and protected by retention/lifecycle rules.
- `pg_dump` helpers must pass credentials through libpq environment variables, not command-line database URLs.
- Files written on hosted web-service disks should be treated as ephemeral unless the platform explicitly provides durable storage.
- Perform and document a restore drill into a non-production database at least quarterly. A backup that has not been restored is not verified recovery.

## Data Integrity and Upgrades

- State-changing endpoints must use POST, PATCH, PUT, or DELETE. GET and HEAD requests must remain safe and idempotent.
- Validate workflow roles on the server, not only in the UI. For example, replenishment must verify both the source reserve cabinet and the target display cabinet.
- Protect a workflow's source record and duplicate check in the same database transaction when concurrent requests could otherwise create duplicate or inconsistent inventory states.
- Never delete a parent record when retained history still references it. Return a clear validation error or provide an explicit archival workflow instead.
- Every model column added after the initial release needs an idempotent migration for supported existing databases, plus a regression test using a pre-migration schema.

## Verification Gate

Before merging a security-sensitive add-on, run:

```bash
python -m py_compile app.py routes/auth.py routes/admin.py routes/inventory.py routes/api.py database/models.py database/tools/cloud_db_download.py database/tools/offsite_backup.py
pytest
pip-audit -r requirements.txt --progress-spinner off
pip-audit -r requirements-tools.txt --progress-spinner off
bandit -r . -x ./tests -ll
if rg -n "innerHTML|insertAdjacentHTML|outerHTML|document\\.write" static/js templates; then exit 1; fi
```

Add or update regression tests for any new auth, CSRF, export, import, backup, dependency, or DOM-rendering behavior.
