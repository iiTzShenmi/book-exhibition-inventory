# EXIS Security Remediation Report

**Date:** 2026-07-19
**Scope:** Goal 2 only. Source-only implementation and verification; no production access, deployment, or production data access occurred.

## 1. Implementation summary

Implemented the confirmed, code-verified Goal 2 findings: invitation expiry and atomic redemption, generic public registration failures, isolated public issue reports with structured validation and low-friction abuse controls, archived-inventory restoration, safe outbound-link referrer handling, and a safe `.env.example` template. No Goal 3 public UI restructuring was performed.

## 2. Confirmed findings fixed

- **I-INV-001:** `/add_book` now finds the retained title/cabinet row and restores archived inventory rather than attempting a duplicate insert.
- **R-EXT-001:** The LINE external link now uses `rel="noopener noreferrer"`.

## 3. Confirmed findings partially fixed

- **R-REG-001:** New invites expire after `ADMIN_INVITE_TTL_HOURS` (default 72 hours), expired legacy unused invites fail closed, and invite consumption is conditional and atomic. The public `/register` route remains by product design pending a decision on internal or unguessable-link onboarding.
- **R-PUB-001:** New reports are separated from `AuditLog`, JSON is schema-checked and normalized, a honeypot and ten-minute content fingerprint deduplication are present, and advanced administrators can read the new report list. Retention, report-state workflow, CAPTCHA/provider selection, and migration of old audit entries require product decisions.
- **R-AUTH-001:** Registration failures now use one public message. Monitoring thresholds and account-lockout policy remain unimplemented by design.

## 4. Findings not changed

- **R-INFO-001:** The public developer contact remains because no approved role mailbox or support workflow was supplied.
- **I-DATA-001:** Tracked operational data was not classified or removed; doing so without classification could destroy required records.
- **R-AUTHZ-001:** The role capability matrix was not changed without a product-approved policy.
- **R-PROD-001, R-DEP-001, R-A11Y-001:** These require production, CI, browser, or assistive-technology evidence.
- **R-UI-IA-001, R-UI-MAP-001, R-UI-CAB-001, R-UI-REG-001:** Deferred as Goal 3/product-decision work.

## 5. Authentication changes

- Added `AdminInvite.expires_at` and a bounded configurable issuance lifetime.
- `find_valid_invite()` rejects used, missing-expiry, and expired invites.
- `claim_invite()` conditionally updates the invite in the same transaction as account creation, so a second redemption cannot succeed after the first claim.
- `/register` returns the same public failure message for invalid input, duplicate username/email, invalid, used, or expired invite codes.
- `tools/create_admin_code.py` now stores and displays an expiry timestamp for each generated invite.

## 6. Authorization changes

No role grants were broadened or removed. The existing `advance-admin` system page now receives up to 200 isolated issue reports; other roles do not receive them. A complete role matrix remains a product decision.

## 7. CSRF and session changes

No new CSRF or session mechanism was added. Existing central CSRF validation and login/session rotation remain in place and were regression-tested. Public reporting continues to require a CSRF token.

## 8. Input and output security changes

- `/api/report_issue` requires a JSON object with exactly `name`, `type`, `description`, and optional honeypot `website` fields.
- It rejects unsupported media types, unexpected fields, invalid types, oversized values, and control characters; permitted descriptions preserve newlines.
- Duplicate reports within ten minutes are accepted without creating another row; a filled honeypot receives the same success response without persistence.
- Reports are rendered with Jinja escaping and whitespace preservation in the advanced-admin system page, not stored as raw public text in the immutable audit trail.
- The LINE external link suppresses opener access and referrer transmission.

## 9. Inventory-integrity changes

`/add_book` now preserves the one-row-per-title/cabinet invariant by reactivating an archived row, clearing `deleted_at`, setting `in_stock=True`, and writing a `restore_book` audit action. The previously reproducible unique-constraint `500` is eliminated for this path.

## 10. Dependency changes

No dependency versions changed. Installed-environment and exact requirement-file `pip-audit` commands completed without reported vulnerabilities. CI remains the release authority for a clean resolver/build evidence trail.

## 11. Database migrations

Added `database/migrations/security_remediation_20260719.py`, an idempotent migration called by the existing reviewed initialization/release path. It:

- Adds nullable `admin_invite.expires_at` to existing deployments.
- Expires unused legacy invites with no expiry instead of granting indefinite validity.
- Adds an `(used_at, expires_at)` lookup index.
- Creates `issue_report` without rebuilding or deleting existing tables.

New model-created databases enforce a non-null expiry for newly issued invites. The migration intentionally does not move existing `AuditLog.action="issue_report"` rows without a retention decision.

## 12. Tests added

- Expired invite rejection, valid one-time redemption, and generic registration failure behavior.
- Conditional single-use invite claim.
- Migration backfill of legacy invite expiry.
- Isolated issue-report persistence, schema rejection, control-character rejection, honeypot, deduplication, and escaped advanced-admin display.
- Archived inventory re-add restoration.
- External-link `noopener noreferrer` assertion.

## 13. Commands executed

- Isolated baseline: `pytest -s` before changes.
- Focused and full isolated test runs: `pytest -s` with testing SQLite configuration.
- `python -m py_compile` for application, routes, models, migration, and operational tools.
- `python -m bandit -r app.py routes database tools -x ./tests -ll -q`.
- DOM-sink guard over `static/js` and `templates`.
- `pip-audit --progress-spinner off`, plus `pip-audit -r requirements.txt` and `pip-audit -r requirements-tools.txt`.
- Tracked-file signature scan for private-key, AWS, GitHub, and Slack token patterns.

## 14. Test results

- Baseline: **46 passed** in the isolated SQLite test configuration.
- Test-first red state: the new regression suite failed collection because `IssueReport` did not exist, as expected before implementation.
- Final full suite: **58 passed, 118 warnings** in 3.39 seconds.
- Compilation, Bandit, DOM-sink guard, installed audit, requirement-file audits, and signature scan completed successfully.
- Bandit emitted two existing narrow `B608` `nosec` warnings and no findings at the configured threshold.
- No failures were caused by the final changes. The historical plain-`pytest` local capture cleanup issue remains avoided by the documented `pytest -s` invocation.
- No type-checker configuration exists. PostgreSQL concurrency, browser/mobile, assistive-technology, CI, and production checks were not executable in this source-only environment.

## 15. Production configuration requirements

Use `.env.example` only as a template. Before release, set independent high-entropy Flask and invite-pepper secrets, secure cookies, trusted hosts, Redis-backed rate limiting, `ADMIN_PASSWORD_HASH`, and `ADMIN_INVITE_TTL_HOURS`. Keep `EXIS_ENABLE_ADMIN_BOOTSTRAP`, `CSRF_DEBUG`, automatic startup initialization, and request-time schema checks disabled. Follow `DEPLOYMENT_CHECKLIST.md` and section 14 of `SECURITY_REMEDIATION_PLAN.md`.

## 16. Rollback considerations

Take a verified database backup first. The migration is additive and does not delete data. Rolling back application code is compatible with the added column/table, but old code would not use expiry or the isolated report table. Do not drop `issue_report` or `expires_at` as a routine rollback step because that would discard evidence or weaken invitation controls. Restore the database only through the documented backup procedure if a release fails.

## 17. Remaining risks

- Public invite redemption remains exposed pending an onboarding design decision.
- Legacy unused invites become invalid at migration time and must be reissued deliberately.
- No PostgreSQL contention test proves multi-worker behavior.
- There is no approved issue-report retention/moderation owner, CAPTCHA policy, role matrix, support mailbox, or operational-data classification.
- Production headers, cookie flags, Redis, proxy IP handling, release command, exact CI resolver audit, and deployed migration outcome still need manual confirmation.

## 18. Recommended commit breakdown

1. Commit the application/model/migration/tool changes and their regression tests.
2. Commit `SECURITY_REMEDIATION_PLAN.md`, this report, `DEPLOYMENT_CHECKLIST.md`, and `.env.example` as reviewable operational documentation.
3. Handle tracked operational data classification/removal, RBAC policy, and Goal 3 UX work in separate reviewed changes.
