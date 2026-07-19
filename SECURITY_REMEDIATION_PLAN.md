# EXIS Security Remediation Plan

**Assessment date:** 2026-07-19
**Scope:** Goal 1 source-only verification of `docs/audits/security_reports/security_report_2026_0719.md`, following `docs/audits/EXIS_SECURITY_UX_ASSESSMENT_1.txt`.
**Boundary:** This document records current source behavior and a remediation plan. It does not assert the state of Render, Redis, PostgreSQL, DNS, browser-delivered headers, or production secrets. No application behavior was changed during this assessment.

## 1. Repository state

| Item | Verified state |
| --- | --- |
| Branch / commit | `fix/exis-audit-findings` at `79915f8bda916b4974ee828148b964a36dafb9b4` |
| Runtime / entry point | Python 3.12.3; Flask application in `app.py` (`app`) |
| Application shape | Flask monolith with `auth`, `inventory`, `api`, and `admin` blueprints |
| Dependencies | Pinned runtime `requirements.txt`, test/audit `requirements-dev.txt`, optional tooling `requirements-tools.txt` |
| Deployment | `Procfile`: explicit `release: ... init-db --no-sync-csv`; web: `EXIS_AUTO_INIT=0 gunicorn app:app` |
| CI | `.github/workflows/security-regression.yml` compiles sources, runs `pytest`, `pip-audit` for runtime/tool requirements, Bandit, and the DOM-sink guard |
| Migration mechanism | Idempotent application functions plus the explicit `database.tools.db_tools init-db` command; no Alembic/Flask-Migrate revision chain |
| Working tree | Dirty before assessment, including application, dependency, UI, database-tool, test, operational-data, and untracked-document changes. They are baseline user work and were not reverted or modified. |

The tracked tree includes `database/inventory.csv`, `database/inventory.db`, and `database/backups/last_auto_backup.json`. Ignore rules do not protect a file that has already been committed.

## 2. Architecture summary

EXIS is a server-rendered Flask application. `app.py` configures Flask, SQLAlchemy, Flask-Limiter, session cookies, request-size limits, CSRF, headers, startup maintenance, and template context. It registers:

- `routes/auth.py`: administrator login, invitation-based registration, logout.
- `routes/inventory.py`: public catalogue home page and administrator cabinet/inventory mutations.
- `routes/api.py`: public search/read APIs, public issue reporting and view tracking, selected administrator APIs, and the service worker.
- `routes/admin.py`: administrator dashboard, imports, backups, events, and audit-log views/export.

SQLAlchemy models in `database/models.py` cover users, invites, audit records, titles, cabinets, inventory, events, snapshots, and backups. PostgreSQL is expected in production through `DATABASE_URL`; local development defaults to SQLite. External trust boundaries are Redis for distributed rate limiting, allowed HTTPS cover-image hosts, a LINE outbound link, optional metadata fetchers, and the optional S3-compatible offsite-backup cron job.

Authentication uses Flask's signed session cookie and Werkzeug password hashes. The application re-reads the current `AdminUser` for an authenticated request (`app.py:1745-1783`), so a deleted account loses access on the next request. The formal role model is `admin`, `manager`, and `advance-admin`; however, most inventory endpoints currently authorize any session with `is_admin`, while sensitive import, backup, and audit endpoints use `_require_roles()` (`routes/admin.py:70-101`).

## 3. Route and attack-surface map

**Global controls:** Flask-Limiter applies a default `300 per minute` limit (`app.py:178-184`). `app.py:1785-1812` requires a session CSRF token for every non-GET/HEAD/OPTIONS request, whether form or JSON. “Admin” below means `session["is_admin"]`; “advanced” and “manager” mean server-side database role verification through `_require_roles()`.

| Route and method | Auth / role | State and CSRF | Input / database effect | Response |
| --- | --- | --- | --- | --- |
| `/` GET | Public | Read-only | Queries titles, inventory, cabinets, events | Home HTML |
| `/search` GET | Public | Read-only | Bounded title/topic/cabinet filters, max 200 results | Search HTML |
| `/book_details/<title>` GET | Public; richer data for admin | Read-only | Looks up active title inventory | Modal HTML / 404 JSON |
| `/api/cabinets` GET | Public | Read-only | Cabinet list | JSON |
| `/api/cabinets/<id>/featured` GET | Public | Read-only | Cabinet and up to eight titles | JSON / 404 |
| `/api/events` GET | Public | Read-only | Active events and book metadata | JSON |
| `/api/book_titles` GET | Public | Read-only | Query length >= 2; maximum 20 matches | JSON |
| `/api/realtime_status` GET | Public | Read-only | Computes replenishment alerts | JSON |
| `/titles/<id>/cover` GET | Public | Read-only | Title and normalized cover URL | JSON / 404 |
| `/api/track_view` POST | Public | DB write; CSRF; `60/min` | Normalized title increments view count with session debounce | JSON |
| `/api/report_issue` POST | Public | DB write; CSRF; `10/hour` | Name <= 80, type allowlist, description <= 1200; stores JSON in `AuditLog` | JSON |
| `/login` GET, POST | Public | Login write; CSRF on POST; `5/min` | Username/password lookup; success/failure audit records | HTML / redirect |
| `/register` GET, POST | Public unless already logged in | Account/invite write; CSRF; `10/hour` | Username, email, password, invite code; consumes invite | HTML / redirect |
| `/logout` POST | Session user | Session clear; CSRF | Audit write when logged in | Redirect |
| `/sw.js` GET | Public | Read-only | Static service-worker delivery with no-cache headers | JavaScript |
| `/book_card/<title>` GET | Admin | Read-only | Active title inventory | HTML / redirect |
| `/api/title_cabinets/<title>` GET | Admin | Read-only | Active title locations | JSON |
| `/api/notifications` GET | Admin | Read-only | Replenishment alerts | JSON (`[]` when unauthenticated) |
| `/toggle_modal_stock/<id>` POST | Admin | Inventory write; CSRF | Active inventory row lookup, stock toggle, audit | JSON |
| `/toggle/<book_id>` POST | Admin | Inventory archive; CSRF | Active inventory row lookup, archive, audit | Redirect |
| `/modify_cabinet/<title>` POST | Admin | Inventory/cabinet write; CSRF | Form action, cabinet name, title; restore/archive/create paths | JSON |
| `/cabinets` GET | Admin | Read-only | All cabinets | JSON |
| `/cabinets` POST | Admin | Cabinet write; CSRF | Name and normalized `display`/`reserve` type | JSON |
| `/cabinets/<id>` PATCH | Admin | Cabinet write; CSRF | JSON name/type; uniqueness checked | JSON |
| `/cabinets/<id>` DELETE | Admin | Cabinet delete; CSRF | Refuses any cabinet with retained inventory history | JSON |
| `/cabinets/<id>/books` GET | Admin | Read-only | Active inventory scoped to cabinet | JSON |
| `/cabinets/<cabinet_id>/books/<book_id>/toggle` PATCH | Admin | Stock write; CSRF | Record is scoped by both IDs; PostgreSQL locks row | JSON |
| `/cabinets/<cabinet_id>/books/<book_id>/adjust` PATCH | Admin | Archive write; CSRF | Only `delta=-1` is accepted | JSON |
| `/cabinets/<cabinet_id>/books/<book_id>/move` PATCH | Admin | Move/archive write; CSRF | Source scoped by both IDs; target checked; PostgreSQL locks source/duplicate | JSON |
| `/replenish/<title>` POST | Admin | Move/toggle write; CSRF | Checks reserve/display types, source ID/cabinet/title consistency | JSON |
| `/cabinets/<cabinet_id>/books/<book_id>` DELETE | Admin | Archive write; CSRF | Record scoped by both IDs | JSON |
| `/add_book` POST | Admin | Title/inventory write; CSRF | Form title, cabinet ID, fixed amount `1` | JSON |
| `/admin` GET | Admin | Read-only | Dashboard search, cabinet data, recent audit logs | HTML |
| `/admin/overview` GET | Admin | Read-only | Aggregate metrics and alerts | HTML |
| `/admin/system` GET | Admin; audit content only advanced | Read-only | Import cleanup; advanced users receive logs/backups | HTML |
| `/admin/add_book_preview` POST | Admin | Read-only/external metadata fetch; CSRF | Title/cabinet; optional author/cover/topic lookups | JSON |
| `/admin/audit` GET | Advanced | Read-only | Latest 200 audit records | HTML |
| `/admin/audit/export` GET | Advanced | Read-only export | CSV-safe audit rows | CSV |
| `/admin/backups` GET | Advanced | Redirect-only | No DB change | Redirect |
| `/admin/backup` POST | Advanced | Backup record write; CSRF | Creates in-database snapshot and optional mounted copy | JSON |
| `/admin/import/preview` POST | Advanced | Temp-file write; CSRF | CSV <= configured size, UTF-8, parsing/validation | HTML |
| `/admin/import` POST | Advanced | Inventory/import/backup writes; CSRF | Password re-entry, preview token, warning acknowledgement | JSON |
| `/admin/import/warnings` GET | Advanced | Read-only temp-file read | Preview warning content | HTML |
| `/admin/import/metadata` POST | Advanced | Temp metadata write; CSRF | Session preview token and bounded metadata fetch | JSON |
| `/admin/events` GET | Admin; POST manager/advanced | Event write on POST; CSRF | Event fields and selected book IDs | HTML / redirect |
| `/admin/events/<id>/update` POST | Manager/advanced | Event write; CSRF | Event fields and book IDs | Redirect |
| `/admin/events/<id>/delete` POST | Manager/advanced | Event delete; CSRF | Event ID | Redirect |
| `/admin/events/reorder` POST | Manager/advanced | Event write; CSRF | JSON list of IDs | JSON |

## 4. Existing positive controls

- Hosted production fails at startup without a configured Flask secret and independent invite-code pepper; it rejects plaintext `ADMIN_PASSWORD` (`app.py:86-115`).
- Hosted rate limiting requires reachable Redis unless an explicit emergency memory fallback is set (`app.py:153-184`). `ProxyFix` is enabled for hosted deployments (`app.py:130-131`).
- Sessions are two hours, `HttpOnly`, `SameSite=Lax`, and default to `Secure` in hosted deployments (`app.py:132-137`). Successful login and registration clear the prior session and create a new CSRF token (`routes/auth.py:22-29`, `87-93`).
- CSRF is centrally enforced and compares tokens using `hmac.compare_digest` (`app.py:1785-1812`). There are no state-changing GET routes in the registered map.
- Headers include strict CSP, `nosniff`, `DENY`, a referrer policy, permissions policy, and hosted HSTS (`app.py:1824-1874`). CSP is compatible with the external-stylesheet login page and has no inline-script allowance.
- Cover URLs are normalized to HTTPS and limited to explicit host patterns (`app.py:1014-1114`); the same sources form `img-src` CSP.
- Password and invite values use Werkzeug hashes; invite lookup is HMAC keyed by a separate pepper and the code is single use (`app.py:1121-1202`, `routes/auth.py:71-85`).
- SQLAlchemy query construction is used throughout routes; the source scan found no CORS configuration or wildcard cross-origin header. Jinja autoescaping and DOM-node/`textContent` rendering are reinforced by the DOM-sink guard.
- Inventory foreign keys, `NOT NULL` columns, a unique `(title_id, cabinet_id)` constraint, model validation, archive preservation, and targeted PostgreSQL locks protect core records (`database/models.py:38-90`, `206-234`; `routes/inventory.py:358-669`).
- CSV export cells are neutralized before export; audit and migration rollback behavior have regression tests.

## 5. Confirmed findings

### R-REG-001: public administrator registration remains an exposed high-value route

- **Original claim:** `/register` is publicly reachable and expands the administrator-account attack surface.
- **Classification:** Partially protected. The route remains public, which confirms the exposure. It is protected by CSRF, `10/hour` limiting, single-use hashed invites, and audit logging.
- **Severity / confidence:** Medium residual risk / High confidence.
- **Evidence:** `routes/auth.py:46-101`; `database/models.py:129-140`; `app.py:1142-1202`.
- **Remaining gap:** `AdminInvite` has no expiry field. Registration also differentiates existing username/email and invalid/used invite errors, which permits low-rate identifier probing.
- **Minimal change:** Decide whether public invite redemption is required. If retained, add `expires_at`, reject expired invites before account creation, make registration failure messages uniform, and retain the current token rate limit.
- **Tests:** Expired invite; used invite; invalid invite; duplicate username/email all return the same public error; successful invite is consumed atomically; concurrent redemption only creates one account.
- **Migration / production setting:** Add nullable/backfilled `expires_at`, index active expiration lookups, then make it non-null for new invites. Keep `INVITE_CODE_PEPPER` present and `EXIS_ENABLE_ADMIN_BOOTSTRAP` unset in production.

### R-PUB-001: anonymous issue reporting is a real public write path, but is not an isolated issue system

- **Original claim:** The public issue form could become a spam, content-injection, or log-pollution path if it lacks validation, CSRF, throttling, and safe output.
- **Classification:** Partially protected.
- **Severity / confidence:** Low to Medium residual risk / High confidence.
- **Evidence:** `routes/api.py:28-62` enforces type/length checks, `10/hour`, and database commit; global CSRF is in `app.py:1785-1812`; feedback is safely rendered with `textContent` and `aria-live` in `static/js/base.js:444-479`; templates autoescape the audit display.
- **Remaining gap:** Public reports are written into `AuditLog`, mixing untrusted reports with security/operations audit history. There is no honeypot/CAPTCHA, server-side character-policy normalization, retention policy, or distinct review workflow.
- **Minimal change:** Create a separate `IssueReport` model/table with submission metadata and moderation status; preserve rate limiting and CSRF. Add a progressive anti-abuse control only if anonymous reporting remains a product requirement.
- **Tests:** Invalid type/length; HTML/control-character payload displayed safely; rate-limit behavior; issue reports absent from security-audit export; moderation authorization.
- **Migration / production setting:** Data migration to move existing `action="issue_report"` audit entries if retention is desired. Configure CAPTCHA/honeypot secret only after choosing a provider; avoid logging that secret.

### R-INFO-001: production UI exposes individual developer contact details

- **Original claim:** The public footer exposes developer identity/contact and collaboration details, increasing social-engineering opportunity.
- **Classification:** Confirmed.
- **Severity / confidence:** Low / High confidence.
- **Evidence:** `templates/base.html:534-545` contains a named individual, direct email address, and tool/vendor collaboration labels.
- **Minimal change:** Replace the personal contact with a monitored role mailbox or support form, and move internal collaboration attribution to non-production documentation if it is not a public product requirement.
- **Tests:** Template assertion that personal addresses are absent and the designated support route remains reachable.
- **Migration / production setting:** No database migration. Establish ownership, response SLA, and phishing-report procedure for the role mailbox.

### I-INV-001: archived inventory cannot be re-added through `/add_book`

- **Original claim:** Not in the July report; found during the required inventory-integrity review.
- **Classification:** Confirmed.
- **Severity / confidence:** Medium functional and data-integrity risk / High confidence.
- **Evidence:** `Inventory` has a unique `(title_id, cabinet_id)` constraint (`database/models.py:38-49`). `/add_book` only checks active records before creating a new row (`routes/inventory.py:465-489`). In an isolated in-memory database, first add returned `200`; after archive, re-add returned `500` with the unique-constraint violation and left zero active records.
- **Minimal change:** Query the retained row regardless of status and restore it (`status="active"`, clear `deleted_at`, set `in_stock=True`) instead of inserting a duplicate. Preserve the one-row-per-title/cabinet invariant and audit the restoration. Apply the same policy to every archive/re-add flow.
- **Tests:** Re-add after `/toggle`, `/adjust`, `/remove`, and duplicate-target move; concurrent add returns a controlled conflict or idempotent success; historical audit remains intact.
- **Migration / production setting:** No schema migration if the single-record history policy is retained. If history requires multiple lifecycles, replace the full unique constraint with a database-specific partial active-row constraint in a tested migration.

### I-DATA-001: operational database/data artifacts remain tracked

- **Original claim:** Not a direct July report finding; found by the required secret/config review.
- **Classification:** Confirmed governance risk.
- **Severity / confidence:** Medium until data classification confirms otherwise / High confidence that the paths are tracked.
- **Evidence:** `git ls-files` includes `database/inventory.csv`, `database/inventory.db`, and `database/backups/last_auto_backup.json`; `.gitignore` and `database/.gitignore` cannot untrack existing files. `SECURITY_CRITERIA.md:39-45` explicitly prohibits this pattern.
- **Minimal change:** Classify the contents, rotate/revoke any embedded operational credentials if discovered, remove generated data from the Git index with a reviewable migration/export path, and add a pre-commit/CI guard for database, backup, and secret artifacts.
- **Tests:** CI fails when a database, backup, or secret-bearing fixture is added outside an approved test-fixture directory.
- **Migration / production setting:** No application migration. Ensure production imports and backup jobs use secret stores and durable off-repo storage.

## 6. Already-protected report items

### R-CSRF-001: report could not observe CSRF tokens

- **Original claim:** `/login`, `/register`, public reporting, and cookie-backed mutations might lack CSRF protection.
- **Classification:** Already protected in source.
- **Severity / confidence:** No source-level residual finding / High confidence.
- **Evidence:** Token generation `app.py:1603-1609`; global verification `app.py:1785-1812`; hidden form tokens in `templates/login.html:20-22`, `templates/register.html:20-22`, and `templates/base.html:242-244`; JSON header support in `static/js/base.js:1492-1518`.
- **Minimal change / tests / migration / production setting:** No feature change. Keep regression tests for missing, attacker-seeded, and first-request tokens (`tests/test_security_regressions.py:55-124`). Require `CSRF_DEBUG` to be unset or `0` in production because debug mode reveals token prefixes. No migration.

### R-AUTH-001: report could not see anti-automation controls

- **Original claim:** Login and registration might lack throttling, delay, monitoring, and consistent credential errors.
- **Classification:** Partially protected.
- **Severity / confidence:** Low to Medium residual risk / High confidence.
- **Evidence:** `/login` is `5/min` and uses one invalid-credential message (`routes/auth.py:14-43`); `/register` is `10/hour` (`routes/auth.py:46-48`); both successes/failures are audited where applicable.
- **Remaining gap:** Registration error messages distinguish duplicate username/email from invalid invite. Audit logs are present but no alert threshold or account-lockout policy is implemented.
- **Minimal change:** Normalize registration failures and define monitoring/alert thresholds before adding a lockout that could enable denial of service.
- **Tests / migration / production setting:** Add rate-limit and generic-error regression tests. No migration. Verify production Redis and correct proxy client IP before relying on the limits.

### R-AUTHZ-001: report could not verify server-side authorization, IDOR, session invalidation, or password storage

- **Original claim:** High-impact authorization/session issues were unknown because the report could not access source or log in.
- **Classification:** Partially protected; source controls are present, but role policy and production behavior require manual confirmation.
- **Severity / confidence:** Medium residual governance risk / High confidence for code controls, Medium for end-to-end behavior.
- **Evidence:** Password hashes and session rotation are in `routes/auth.py:21-29`, `75-93`; authenticated sessions are revalidated against `AdminUser` on each request in `app.py:1745-1783`; sensitive routes use database-backed roles in `routes/admin.py:70-101`; inventory object IDs are scoped by cabinet in `routes/inventory.py:358-669`.
- **Minimal change:** Define a capability matrix for `admin`, `manager`, and `advance-admin`, then apply one common server-side authorization helper to inventory operations as well as the already role-gated admin routes.
- **Tests:** For every protected route, test anonymous, `admin`, `manager`, and `advance-admin`; test deleted and demoted accounts; test mismatched cabinet/book IDs and cross-object mutation attempts.
- **Migration / production setting:** No schema migration for the matrix itself. Use HTTPS, secure cookies, trusted hosts, and a rotating `FLASK_SECRET_KEY`; browser checks must verify cookie flags on the deployed site.

### R-EXT-001: third-party cover images and LINE outbound link

- **Original claim:** External images/links need CSP, referrer controls, and minimized trusted sources.
- **Classification:** Partially protected.
- **Severity / confidence:** Low residual risk / High confidence.
- **Evidence:** Cover URLs are HTTPS and allowlisted in `app.py:1014-1114`; CSP `img-src` is generated from that same list and `Referrer-Policy` is set in `app.py:1824-1866`; the LINE link has `rel="noopener"` (`templates/base.html:366`).
- **Remaining gap:** The third-party dependency remains intentional, and the outbound link does not include `noreferrer`; `strict-origin-when-cross-origin` still exposes the origin by design.
- **Minimal change:** Confirm that source-only referrer disclosure is acceptable. Otherwise use `rel="noopener noreferrer"`, remove the link, or proxy/cache covers only after evaluating licensing, availability, and operational cost.
- **Tests / migration / production setting:** Add header/host-policy tests for every configured cover host. No migration. Keep `ALLOWED_COVER_HOSTS` narrow and review additions as trust-boundary changes.

### R-UI-FORM-001: issue-report feedback state was not visible in the passive report

- **Original claim:** The report did not observe sending, success, error, and retry feedback.
- **Classification:** Already protected for client feedback; registration guidance remains incomplete.
- **Severity / confidence:** Low / High confidence.
- **Evidence:** `static/js/base.js:444-479` disables the submit button, sets sending/success/error text, uses a toast, and updates the `aria-live` status at `templates/base.html:524`.
- **Minimal change / tests / migration / production setting:** Preserve current feedback behavior with a browser test. Update the register copy separately under R-REG-001. No migration or production setting.

## 7. Potential findings requiring manual verification

### R-PROD-001: production deployment may differ from source defaults

- **Original claim:** The passive report could not verify session flags, headers, Render settings, debug state, CORS, secrets, dependencies, migrations, or rate-limit storage.
- **Classification:** Needs manual verification.
- **Severity / confidence:** High if fail-closed settings were bypassed / Medium confidence because this is an environment question.
- **Evidence:** Source has the expected controls in `app.py:86-184`, `132-151`, and `1824-1866`; CI declares a safe testing environment in `.github/workflows/security-regression.yml:14-18`. Source cannot prove the configured Render values or on-wire headers.
- **Minimal change:** Run the production checklist in section 14 against staging, then production through approved operations access.
- **Tests:** HTTPS browser/session inspection; unauthenticated and role-matrix smoke tests; deployed-header check; Redis limiter health; release-command confirmation.
- **Migration / production setting:** No source migration. Required settings are listed in section 14.

### R-DEP-001: dependency result must be confirmed from exact requirement resolution

- **Original claim:** The passive report could not inspect dependencies or lockfiles.
- **Classification:** Partially protected; exact local requirement-file audit remains inconclusive.
- **Severity / confidence:** Medium supply-chain process risk / High confidence in the limitation.
- **Evidence:** Runtime and tool dependencies are pinned. The isolated installed-environment `pip-audit` reported no known vulnerabilities. That environment contains `psycopg2-binary==2.9.9`, while current source requires `psycopg2==2.9.9`; therefore its clean result must not be represented as an exact resolver audit of `requirements.txt`. The required `pip-audit -r requirements.txt` invocation did not produce an interpretable result in this local shell. CI is configured to perform the exact runtime and tooling audits.
- **Minimal change:** Make the CI job's `pip-audit -r requirements.txt` and `-r requirements-tools.txt` mandatory for merge, record the successful run, and recreate the local audit venv from the exact requirement files when investigating a release.
- **Tests / migration / production setting:** No migration. Review Dependabot updates and build-system packages (notably PostgreSQL headers for `psycopg2`) in the same environment used by CI/Render.

### R-A11Y-001: keyboard focus and table semantics

- **Original claim:** Focus visibility and table accessibility could not be verified by passive browsing.
- **Classification:** Partially protected; requires manual assistive-technology testing.
- **Severity / confidence:** Medium UX/accessibility risk / Medium confidence.
- **Evidence:** Explicit `:focus-visible` styles exist in `static/css/login.css:142-145` and `static/css/main.css:934-936`. Mobile styling reflows audit rows (`static/css/admin.css:1345-1383`), but the audit display itself uses `div` grid elements (`templates/admin_system.html:82-96`) rather than a semantic table/caption.
- **Minimal change:** Complete a keyboard and screen-reader pass at desktop and mobile sizes. If the audit list is data-tabular, adopt semantic table markup with responsive labels or an accessible list pattern.
- **Tests / migration / production setting:** Add Playwright/axe checks for focus order, visible focus, modal focus trapping, and labels. No migration or production setting.

### R-UI-IA-001: public home page contains many simultaneous modules

- **Original claim:** Search competes with alerts, events, popular titles, discounts, recommendations, reporting, documentation, and developer information.
- **Classification:** Confirmed source structure; priority requires product validation.
- **Severity / confidence:** High UX priority / High confidence for structure, Medium for real user impact.
- **Evidence:** The search, map, real-time, events, popular-title, discount, recommendation, footer-report, documentation, and developer blocks are all in `templates/base.html:69-550`; `/` supplies data for several modules in `routes/inventory.py:34-64`.
- **Minimal change:** Measure task completion for “find title/location/availability,” make search/results the primary first viewport, and move secondary content behind tabs, disclosure controls, or lower-priority pages.
- **Tests / migration / production setting:** Desktop/mobile visual regression and task tests. No migration or production setting.

### R-UI-MAP-001: disabled map/placeholder remains visible as a product-status signal

- **Original claim:** A disabled map and placeholder asset make the production experience appear unfinished.
- **Classification:** Confirmed.
- **Severity / confidence:** Medium UX risk / High confidence.
- **Evidence:** The direct quick-guide control is hidden (`templates/base.html:79`), but a placeholder floor plan remains at `templates/base.html:124-138`; the footer exposes “部分功能停用中” and names the map in the disabled list (`templates/base.html:461-480`).
- **Minimal change:** Either ship a maintained, data-backed map or remove the dormant map/status surface from the public experience. A disabled function should not appear as an available destination.
- **Tests / migration / production setting:** Mobile/desktop visual tests confirm the chosen state. No migration or production setting.

### R-UI-CAB-001: cabinet filter remains a long native select

- **Original claim:** A dense, long cabinet selector is difficult on mobile and high-frequency workflows.
- **Classification:** Confirmed.
- **Severity / confidence:** High UX priority / High confidence.
- **Evidence:** `templates/base.html:96-101` renders every cabinet into one select; the public home route supplies every cabinet (`routes/inventory.py:53-64`).
- **Minimal change:** Define grouping/search/recent-use requirements, then introduce a keyboard-accessible searchable picker only if the number and naming of cabinets justify it.
- **Tests / migration / production setting:** Large-cabinet fixture, keyboard selection, mobile overflow, and no-JavaScript fallback tests. No migration or production setting.

### R-UI-REG-001: registration instructions remain too abstract for an invite-only workflow

- **Original claim:** The registration form requires Gmail and a “security code,” but does not clearly identify the intended audience, the invitation process, or useful recovery guidance.
- **Classification:** Partially protected.
- **Severity / confidence:** Medium UX risk / High confidence.
- **Evidence:** The form has visible labels and a CSRF token, but its primary guidance is only “輸入安全碼建立新的管理帳號” and “請聯絡網站擁有者” (`templates/register.html:13-42`).
- **Minimal change:** State that the page is for invited staff only, say how an invite is issued/reissued, describe the Gmail requirement only if it is a real policy, and give a privacy-safe support path. Keep public failure messages generic as required by R-REG-001.
- **Tests / migration / production setting:** Template/accessibility assertion for labels, instructions, and generic failures; browser test at phone and desktop widths. No migration. Publish the approved support channel in production configuration/documentation.

## 8. Not-applicable report items

| Report item | Classification and reason | Follow-up |
| --- | --- | --- |
| Repository inaccessible to the passive assessment | Not applicable now. The current checkout is available, imported in a controlled test configuration, and mapped above. | Keep review evidence tied to a commit and avoid assuming this verifies the deployed host. |
| Cross-tenant/object-owner IDOR | Not applicable to the current data model: there are no public user-owned resources or tenant ownership fields. | Continue to scope mutable inventory records by both cabinet and record ID; revisit if user/tenant ownership is introduced. |
| Wildcard CORS concern | Not applicable in current source: no Flask-CORS or `Access-Control-Allow-Origin` configuration was found. | Verify reverse proxy/CDN rules do not add permissive CORS headers. |
| “No visible CSRF token” inference | Superseded by source evidence; tokens are rendered as hidden/meta values and checked centrally. | Retain regression coverage rather than adding duplicate CSRF systems. |

## 9. Dependency findings

- **Pinned surface:** Runtime, dev/audit, and optional tooling requirements are separated. `Pillow==12.3.0` in tooling is newer than the vulnerable `12.0.0` from the previously reported CI output.
- **Completed baseline:** Installed-environment `pip-audit` returned “No known vulnerabilities found.” This is useful evidence only for the installed audit environment, not proof that the exact requirement resolver was audited.
- **Outstanding verification:** Recreate an exact CI-like venv from `requirements-dev.txt` with system PostgreSQL headers, then require both `pip-audit -r requirements.txt` and `pip-audit -r requirements-tools.txt` to pass. Store the CI run URL/artifact with a release.
- **Bandit:** Source scan of `app.py`, `routes`, `database`, and `tools` passed at `-ll`. Bandit reported two `# nosec B608` annotations without failed tests; each suppression must remain narrowly justified and reviewed when SQL changes.
- **Dependency decision:** Continue using source-built `psycopg2` rather than adding `psycopg2-binary` as a production dependency, and ensure the Render/CI build image supplies `libpq` headers.

## 10. Secret/config findings

- `.env` is ignored and was not read or displayed. A tracked-file signature scan found no conventional private-key, AWS access-key, GitHub-token, Google-key, or Slack-token signatures.
- This is not historical-secret proof: `gitleaks` is not installed locally, and the scan did not inspect production secret-store values or all commit content.
- Hosted production source code fails closed for Flask secret, invite pepper, plaintext admin password, and Redis rate limiting. It masks database/Redis URLs in selected log messages.
- `tools/create_admin_code.py` intentionally prints a newly generated invite once. Treat terminal scrollback, CI logs, recordings, and support captures as secret-bearing when that command is used.
- The tracked operational-data paths in section 5 must be classified and removed from normal source history. Do not print their contents into audit logs or issue tickets.

## 11. Inventory-integrity findings

- The model correctly rejects null/invalid foreign IDs and prevents duplicate title/cabinet rows. Cabinet deletion preserves historical inventory records; move and replenish validate cabinet types and title relationships.
- PostgreSQL mutation paths use row locks for selected high-contention operations. SQLite test coverage validates move, invalid replenishment, retained archived inventory, and legacy quantity behavior.
- **Confirmed defect:** I-INV-001 prevents reactivation through `/add_book` after archive. It must be fixed before any broad data-import or operational expansion.
- **Residual concurrency risk:** `move_cabinet_book` obtains source then target-duplicate locks in request-dependent order. Test inverse concurrent moves against PostgreSQL and standardize lock ordering/retry handling if deadlocks are observed.
- **Migration risk:** Schema changes remain imperative/idempotent functions. The web process correctly disables automatic startup mutation in `Procfile`, but production rollout still depends on the release step being run exactly once and successfully.

## 12. Test baseline

| Check | Result | Notes |
| --- | --- | --- |
| `pytest -s` in isolated SQLite configuration | Pass: 46 tests | 74 deprecation warnings, primarily `datetime.utcnow`; not a security failure but should be addressed before Python removes the API. |
| Plain `pytest` | Could not complete locally | Pytest capture cleanup raised local `FileNotFoundError` after collection; `pytest -s` is the stable local invocation and completed all tests. |
| Python compilation | Pass | Compiled app, routes, models, services, and operational tools. |
| Bandit source scan at `-ll` | Pass | Two `nosec B608` warning annotations require continued review. |
| Frontend DOM-sink guard | Pass | No `innerHTML`, `insertAdjacentHTML`, `outerHTML`, or `document.write` in `static/js` or templates. |
| Installed-environment `pip-audit` | Pass | No known vulnerabilities in the installed audit environment. |
| Exact `pip-audit -r requirements*.txt` local capture | Inconclusive | The required runtime-file invocation did not return an interpretable local result; CI remains the required exact resolver check. |
| Tracked-secret signature scan | Pass for patterns scanned | No conventional key/token signature files found; not a history or deployment-secret audit. |
| Browser/mobile/production tests | Not run | No deployed host or browser-assisted manual validation was used for this source-only Goal 1 work. |

## 13. Prioritized implementation plan

| Priority | Work | Minimal implementation / acceptance criteria |
| --- | --- | --- |
| P0 | Remove tracked operational data | Classify and remove tracked SQLite, CSV, and backup metadata from normal source history; add CI/pre-commit guard; rotate any exposed secret if classification finds one. |
| P0 | Fix archived re-add behavior | Restore retained inventory row in `/add_book`; add re-add and concurrent-add regression tests; return controlled conflict/idempotent result rather than `500`. |
| P1 | Complete invite lifecycle | Add expiration, generic public registration failures, atomic redemption test, and a documented invitation issuance/revocation process. |
| P1 | Separate public issue reports | Add dedicated data model/workflow, moderation/retention policy, structured validation, and anti-abuse control chosen by product decision. |
| P1 | Define and enforce RBAC matrix | Specify allowed capabilities for all three roles, refactor inventory routes to a common authorization helper, and add a full route/role matrix test. |
| P1 | Verify production controls | Complete section 14 in staging and production; attach evidence to release/change record. |
| P2 | Formalize migration lifecycle | Adopt a versioned migration tool or an equivalent reviewed release migration contract; prohibit schema mutation on request paths; test PostgreSQL upgrade/rollback. |
| P2 | Make dependency evidence release-grade | Require exact CI audits and add a secret scanner; record audit results with every deployment. |
| P2 | Test PostgreSQL contention | Run concurrent move/add/replenish tests against PostgreSQL, standardize locks, and handle deadlocks safely. |
| P3 | Simplify public information architecture | Prioritize search/results, decide the map's status, improve cabinet filtering, and validate the design on phone and desktop. |
| P3 | Complete accessibility verification | Keyboard, screen-reader, focus, modal, and responsive table/list testing; add automated accessibility smoke checks. |

## 14. Production configuration requirements

Before a production deployment, operations must verify all of the following without placing values in source control or logs:

1. Set `APP_ENV=production` or the hosted equivalent; set a high-entropy `FLASK_SECRET_KEY`/`APP_SECRET_KEY` and an independent high-entropy `INVITE_CODE_PEPPER`.
2. Use `ADMIN_PASSWORD_HASH`, not `ADMIN_PASSWORD`; keep `EXIS_ENABLE_ADMIN_BOOTSTRAP` disabled after intentional bootstrap.
3. Configure a reachable `REDIS_URL`; do not set `EXIS_ALLOW_MEMORY_RATE_LIMIT=1` except a documented, short emergency exception. Validate client IP/rate-limit behavior through the hosting proxy.
4. Set `SESSION_COOKIE_SECURE=1`, retain `SESSION_COOKIE_HTTPONLY=True` and `SESSION_COOKIE_SAMESITE=Lax`, and verify the issued HTTPS cookie attributes in a browser.
5. Set `TRUSTED_HOSTS`/`EXIS_TRUSTED_HOSTS` to the approved domain set and confirm proxy host/scheme forwarding. Do not enable `CSRF_DEBUG` in production.
6. Execute `python -m database.tools.db_tools init-db --no-sync-csv` as the reviewed release/pre-deploy step. Keep web startup at `EXIS_AUTO_INIT=0` and request schema checks disabled.
7. Verify deployed CSP, HSTS, `X-Frame-Options`, `nosniff`, referrer policy, permissions policy, and cover-source allowlist on HTTPS responses.
8. Keep `ALLOWED_COVER_HOSTS` limited to approved HTTPS providers; review each addition for privacy, availability, and licensing.
9. Configure independent, encrypted, versioned, retention-protected offsite backups and perform a documented non-production restore drill at least quarterly. In-app `BackupArchive` is not disaster recovery.
10. Require passing exact CI dependency audits, tests, static scan, DOM-sink guard, and secret scan before deployment; retain the evidence with the release.

## 15. Questions requiring product decision

1. Should `/register` remain publicly discoverable for invite redemption, or should invites use an unguessable acceptance URL or an internal onboarding workflow?
2. What invite lifetime, revocation behavior, and role assignment policy are required for temporary exhibition staff?
3. Must anonymous issue reporting remain open? If yes, what privacy notice, retention period, moderation owner, and anti-abuse control are acceptable?
4. What capabilities distinguish `admin`, `manager`, and `advance-admin`, especially for stock mutation, cabinet management, imports, exports, and backups?
5. Is archive/re-add meant to preserve one enduring inventory record per title/cabinet, or must the system preserve multiple lifecycle records? This decides the I-INV-001 fix and constraint strategy.
6. Is the floor-plan/map feature going to be maintained for the next event? If not, should all related status/modal/placeholder content be removed from public UI?
7. Which cabinet groups, recent-use behavior, and mobile search interactions are needed before replacing the native selector?
8. Should public external links send the site origin as referrer, or is `noreferrer` required? Which cover providers are contractually approved?
9. Which role mailbox or support workflow should replace the individual developer contact shown in the public footer?

## 16. Goal 2 completion status (2026-07-19)

This source-only status matrix records the final disposition of every finding in
this plan. It does not claim deployed-host verification.

| Finding | Final status | Goal 2 disposition |
| --- | --- | --- |
| R-REG-001 | Partially fixed | Added expiry, fail-closed legacy handling, generic errors, and conditional invite claim. Public registration remains pending onboarding product decision. |
| R-PUB-001 | Partially fixed | New reports use `IssueReport` with schema validation, honeypot, deduplication, safe display, and advanced-admin review. Retention, CAPTCHA/provider choice, report workflow, and historic-audit migration require product decision. |
| R-INFO-001 | Requires product decision | No approved role mailbox/support workflow was supplied. |
| I-INV-001 | Fixed | `/add_book` restores the retained archived row and has a regression test. PostgreSQL concurrency remains separately unverified. |
| I-DATA-001 | Requires product decision | Operational artifacts were not classified or removed without verified ownership/retention requirements. |
| R-CSRF-001 | Already protected | Existing centralized CSRF implementation remains unchanged and regression-tested. |
| R-AUTH-001 | Partially fixed | Registration errors are generic; rate limits already exist. Alerting/lockout policy and production Redis verification remain outstanding. |
| R-AUTHZ-001 | Requires product decision and manual verification | No role matrix was approved; deployed role behavior still needs a controlled route/role pass. |
| R-EXT-001 | Fixed | Existing CSP/cover allowlist protections remain; LINE now uses `noopener noreferrer`. |
| R-UI-FORM-001 | Already protected | Existing client feedback behavior remains unchanged. |
| R-PROD-001 | Requires production configuration and manual verification | Source controls cannot verify deployed secrets, proxy, Redis, cookies, headers, or release command. |
| R-DEP-001 | Requires manual verification | Local audits passed; CI/build-image evidence is still required for a release. |
| R-A11Y-001 | Requires manual verification | Browser and assistive-technology validation was not part of Goal 2. |
| R-UI-IA-001 | Requires product decision | Deferred to Goal 3; no public information-architecture restructuring performed. |
| R-UI-MAP-001 | Requires product decision | Deferred to Goal 3. |
| R-UI-CAB-001 | Requires product decision | Deferred to Goal 3. |
| R-UI-REG-001 | Requires product decision | Invite guidance/support copy needs an approved onboarding and support process. |
| Repository-access limitation | Not applicable | This local checkout was available for source verification. |
| Cross-tenant/object-owner IDOR | Not applicable | Current model has no public user-owned resources or tenant ownership field. |
| Wildcard CORS concern | Not applicable | No application CORS configuration was found; proxy/CDN still needs release verification. |
| No-visible-CSRF-token inference | Not applicable | Superseded by source evidence and regression coverage. |
