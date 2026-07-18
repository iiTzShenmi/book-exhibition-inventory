# EXIS Test Commands

## Environment Assumptions

- Repository root: current EXIS checkout.
- Python: `/tmp/exis-review-venv/bin/python` with test/runtime requirements installed.
- Test mode: `APP_ENV=testing`, `EXIS_SKIP_STARTUP_INIT=1`, `DATABASE_URL=sqlite:///:memory:`, `FLASK_SECRET_KEY=review-secret`.
- No production writes, authentication attempts, brute-force requests, or destructive operations were performed.

## Executed Commands and Results

```bash
APP_ENV=testing EXIS_SKIP_STARTUP_INIT=1 DATABASE_URL='sqlite:///:memory:' \
  FLASK_SECRET_KEY=review-secret /tmp/exis-review-venv/bin/python -m pytest -q -s
```

Result: pass. The collection contained 42 tests: 2 auth, 5 inventory move, 3 model, 5 offsite backup, and 27 security regression tests. Warnings were only naive-UTC deprecations.

```bash
APP_ENV=testing EXIS_SKIP_STARTUP_INIT=1 DATABASE_URL='sqlite:///:memory:' \
  FLASK_SECRET_KEY=review-secret /tmp/exis-review-venv/bin/python -m pytest -q -s \
  tests/test_security_regressions.py tests/test_inventory_move.py
```

Result: pass. Covers new CSRF/HTTP-method, read-only book details, CSP action, legacy schema, migration preservation and failed-candidate rollback, replenishment, cabinet history, and quantity-contract regressions.

```bash
cp database/backups/inventory_20251126_025509.db /tmp/exis-historical-migration-fixed.db
APP_ENV=testing EXIS_SKIP_STARTUP_INIT=1 \
  DATABASE_URL=sqlite:////tmp/exis-historical-migration-fixed.db \
  FLASK_SECRET_KEY=review-secret \
  /tmp/exis-review-venv/bin/python -m database.tools.db_tools init-db --no-sync-csv
```

Result: pass. The historical snapshot had 720 inventory rows before and after migration. `book_title` gained required columns and local `/` returned HTTP 200.

```bash
git ls-files '*.py' | xargs /tmp/exis-review-venv/bin/python -m py_compile
/tmp/exis-review-venv/bin/python -m bandit -q -r app.py routes database/tools -x ./tests -ll
git diff --check
```

Result: pass. Bandit emitted two existing reviewed `#nosec B608` notices in tooling, without a failing issue.

```bash
npx --yes acorn --ecma2022 static/js/base.js
npx --yes acorn --ecma2022 static/js/admin_dashboard.js
npx --yes acorn --ecma2022 static/js/search_results.js
if rg -n "innerHTML|insertAdjacentHTML|outerHTML|document\\.write" static/js templates; then exit 1; fi
```

Result: pass. Changed JavaScript parsed and the DOM-sink guard found no disallowed sinks.

```bash
# Fresh temporary SQLite seed, then local Flask server on 127.0.0.1:5055
npx --yes playwright screenshot -b chromium --viewport-size '1280,720' http://127.0.0.1:5055/ audit-home.png
npx --yes playwright screenshot -b chromium --viewport-size '390,844' --full-page \
  'http://127.0.0.1:5055/search?q=%E8%A8%AD%E8%A8%88' audit-search-mobile.png
```

Result: pass. Visual inspection confirmed public search and an approved CWGV cover. The first mobile capture exposed the hidden quick-guide overlap; a CSS fix was applied and the rerun was clear. Temporary images and server were removed/stopped.

```bash
curl -I https://book-exhibition-inventory.onrender.com/
```

Result: non-destructive header sample returned HTTP 200 with CSP, HSTS, secure cookie flags, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, and no-store. This did not test protected production workflows.

## Inconclusive or Not Executed

```bash
/tmp/exis-review-venv/bin/python -m pip_audit -r requirements.txt --progress-spinner off
```

Result: inconclusive. `pip-audit` stalled while creating/upgrading its temporary virtual environment and was interrupted after 90 seconds without advisory output. Re-run this in CI or a network-stable build environment; do not interpret the interruption as a clean audit.

Not executed because required infrastructure or authorization was unavailable:

- Authenticated production operations, role lifecycle, import commit, event changes, and real browser undo/back/forward journeys.
- Two-client PostgreSQL contention testing for move/replenish locks.
- Render environment-variable inspection and release-command verification.
- S3-compatible offsite backup upload and restore drill.
- Production DNS, custom-domain, and network-layer configuration review.

## Recommended Merge Gate

```bash
python -m py_compile app.py routes/auth.py routes/admin.py routes/inventory.py routes/api.py database/models.py database/tools/cloud_db_download.py database/tools/offsite_backup.py
pytest
pip-audit -r requirements.txt --progress-spinner off
pip-audit -r requirements-tools.txt --progress-spinner off
bandit -r . -x ./tests -ll
if rg -n "innerHTML|insertAdjacentHTML|outerHTML|document\\.write" static/js templates; then exit 1; fi
```
