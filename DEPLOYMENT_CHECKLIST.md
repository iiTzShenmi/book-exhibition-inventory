# EXIS Deployment Checklist

Use this checklist for a reviewed staging or production release. Do not place values for any secret in source control, tickets, shell history, or logs.

## Backup requirements

- [ ] Confirm a current, encrypted, off-repo database backup exists and can be restored in a non-production environment.
- [ ] Record the backup location and restore owner in the release record, without credentials.
- [ ] Confirm the release does not rely on `BackupArchive` as disaster recovery.

## Environment variables

- [ ] Set production environment mode and approved trusted hosts.
- [ ] Set independent high-entropy Flask session and invite-code pepper secrets.
- [ ] Set `ADMIN_PASSWORD_HASH`; do not set plaintext `ADMIN_PASSWORD`.
- [ ] Set a reachable Redis URL for rate limiting.
- [ ] Set secure-cookie behavior and keep `CSRF_DEBUG` disabled.
- [ ] Set `ADMIN_INVITE_TTL_HOURS` to the approved invitation lifetime.
- [ ] Keep automatic startup initialization and request-time schema checks disabled for the web process.
- [ ] Keep administrator bootstrap disabled after intentional recovery/bootstrap work.

## Secret rotation

- [ ] Rotate a secret through the approved secret store if data classification or release review identifies exposure.
- [ ] Reissue affected admin invites after an invite-pepper rotation or migration of legacy invite records.
- [ ] Confirm invite creation output is not retained in CI logs, recordings, or support captures.

## Migration commands

- [ ] Run the reviewed release command: `python -m database.tools.db_tools init-db --no-sync-csv`.
- [ ] Confirm `security_remediation_20260719` completes and the release log contains no migration error.
- [ ] Confirm `admin_invite.expires_at`, `ix_admin_invite_unused_expiry`, and `issue_report` exist using approved database access.
- [ ] Do not run destructive schema rollback commands as a routine recovery action.

## Dependency installation

- [ ] Build from the pinned requirements in the deployment image.
- [ ] Run `pip-audit -r requirements.txt --progress-spinner off`.
- [ ] Run `pip-audit -r requirements-tools.txt --progress-spinner off` where tooling is installed.
- [ ] Retain the passing CI audit, tests, static scan, and secret-scan evidence with the release.

## Static asset steps

- [ ] Deploy the matching application and static asset revision together.
- [ ] Verify `/sw.js` responds with the expected no-cache behavior.
- [ ] Hard-refresh a public page and verify approved book-cover hosts load without CSP errors.
- [ ] Verify the external LINE link opens with `noopener noreferrer` behavior.

## Smoke tests

- [ ] Open `/`, search a known title, and verify title, cabinet, stock state, and cover behavior.
- [ ] Submit one valid public issue report with a test-only marker; verify it appears only in the advanced-admin issue-report view, not audit export.
- [ ] Submit the same marker again within ten minutes; verify only one report is created.
- [ ] Submit a filled honeypot in a controlled test; verify it returns success and persists no report.

## Login and logout checks

- [ ] Verify login rejects a missing or invalid CSRF token.
- [ ] Verify successful login rotates the session and CSRF token.
- [ ] Create a short-lived admin invite through the approved tool; confirm its stored expiry matches policy.
- [ ] Verify expired, reused, malformed, duplicate-account, and invalid invite attempts receive the same public registration failure.
- [ ] Verify a valid invite creates one account and cannot be redeemed again.
- [ ] Verify logout is POST-only, requires CSRF, clears the session, and cannot access admin pages afterward.

## Inventory mutation checks

- [ ] Archive one non-critical test inventory record, re-add it through `/add_book`, and verify the original record is active, in stock, and has no `deleted_at` value.
- [ ] Verify the re-add action produces a `restore_book` audit record.
- [ ] Verify invalid amount, same-location move, mismatched cabinet/book ID, and duplicate-target move return controlled errors.

## Security-header checks

- [ ] Verify HTTPS responses include the deployed CSP, HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, Referrer-Policy, and Permissions-Policy.
- [ ] Verify session cookies are `Secure`, `HttpOnly`, and `SameSite=Lax`.
- [ ] Verify no permissive CORS policy is added by the proxy/CDN.
- [ ] Verify the cover-source allowlist matches only approved HTTPS providers.

## Rollback steps

- [ ] Stop release progression on migration, header, smoke-test, or authorization failure.
- [ ] Preserve logs and database state for investigation without copying secrets or public report contents into tickets.
- [ ] Roll back application/static code to the last known-good revision only after confirming it remains compatible with the additive schema.
- [ ] Restore the verified pre-release backup only through the approved procedure when data recovery is required.
- [ ] Re-run the smoke, login/logout, inventory, and header checks after rollback.
