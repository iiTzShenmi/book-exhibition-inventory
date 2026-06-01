# 書展庫存系統 (Book Inventory System)

Flask-based inventory management system for book exhibitions. This single README collects the runbook, database notes, helper tools, and prototypes that were previously scattered across multiple files.

## Quick Start

### Production (Render)
- PostgreSQL via `DATABASE_URL` is the single source of truth.
- CSV is only for one-time imports/exports.
- Run schema/bootstrap maintenance explicitly before starting the web server:
  ```bash
  python -m database.tools.db_tools init-db --no-sync-csv
  ```
- Start the web process with startup mutation disabled:
  ```bash
  EXIS_AUTO_INIT=0 gunicorn app:app
  ```

### Development (Local)
- SQLite at `database/inventory.db`.
- CSV sync is available for testing and local imports.
- Local startup still runs the lightweight bootstrap by default. To match production behavior locally:
  ```bash
  python -m database.tools.db_tools init-db --no-sync-csv
  EXIS_AUTO_INIT=0 flask run --debug --no-reload --host=0.0.0.0
  ```

## Database Architecture & Operations

Architecture:
```
Production: PostgreSQL (DATABASE_URL) ← Flask App
Development: SQLite (database/inventory.db) ← Flask App
CSV: database/inventory.csv (one-time import/export only)
```

Important notes:
1. CSV is **not automatically synced** in production.
2. All production writes go directly to PostgreSQL.
3. CSV exports are disabled by default (`ENABLE_CSV_EXPORT=1` to enable).
4. CSV imports are disabled by default in production (`ENABLE_CSV_SYNC=1` to enable).

Common commands (run from repo root):
```bash
# Explicit schema/bootstrap step
python -m database.tools.db_tools init-db --no-sync-csv

# Health check / maintenance
python -m database.tools.db_tools check
python -m database.tools.db_tools dedupe
python -m database.tools.db_tools purge-null

# Data import/export (dev)
ENABLE_CSV_SYNC=1 python -m database.tools.db_tools sync-csv
ENABLE_CSV_EXPORT=1 python -c "from app import app, export_db_to_csv; app.app_context().push(); export_db_to_csv()"

# Database sync & diagnostics
python database/tools/db_sync.py upload          # upload local SQLite -> production
python database/tools/db_sync.py push --auto-fix # validate, clean, upload
python database/tools/db_sync.py diagnose        # compare local/cloud
python database/tools/db_sync.py clean --purge-null --dedupe --auto-fix
python database/tools/cloud_db_download.py --output database/backups/render_dump.sql

# Concise local security regression checks
pytest
```

Key files:
- `database/models.py` - SQLAlchemy models
- `database/services.py` - Database service layer
- `database/tools/` - Maintenance scripts (db_tools, db_sync, cloud_db_download)
- `database/backups/` - Database backups

## Tools (helper scripts in `tools/`)

Run all commands from the repo root. Dependencies come from `pip install -r requirements.txt`.

- `fetch_cover_url.py`: Fill missing cover links by scraping bookzone.cwgv.com.tw. Supports `--title`, `--limit`, `--verbose`, `--force`, `--dry-run`, and manual overrides via `--set-title`/`--set-cover`. Retries with spacing variants if the initial query fails. Missing results are logged to `tools/missing_covers.txt`.
- `fetch_author.py`: Populate missing `author` fields. Supports `--title`, `--limit`, `--verbose`, `--force`, `--dry-run`, and manual set via `--set-title`/`--set-author`. Logs misses to `tools/missing_authors.txt`.
- `fetch_topics.py`: Cache topics/tags from book detail pages into `tools/topics_cache.json`. Supports `--title`, `--limit`, `--verbose`, `--force`, `--dry-run`, and manual set via `--set-title`/`--set-topics`. Logs misses to `tools/missing_topics.txt`.
- `generate_security_code.py`: Create admin security codes from username/email.
- `create_admin_code.py`: Create invite codes without needing user details (e.g., `python tools/create_admin_code.py --memo "for Alice"`).

## Similarity Module

Provides search suggestions when no direct results are found (auto-called in `/search`). It scores by title similarity and topic overlap, returning the top matches.
- Key function: `suggest_for_missing_title(profiles, query, top=5)` returns `(score, BookProfile)` tuples.
- Scoring weights: title 65%, topic overlap 35%, plus bonuses for title substrings and topic hits.
- `BookProfile` fields: `title`, `topics`, `author`, `cabinet`, `in_stock`.

## Project Structure

```
Web/
├── app.py                 # Main Flask application
├── database/              # Database layer
│   ├── models.py          # SQLAlchemy models
│   ├── services.py        # Service layer
│   ├── tools/             # Maintenance scripts
│   └── backups/           # Database backups
├── tools/                 # Utility scripts
├── static/                # CSS/JS assets
├── templates/             # Jinja2 templates
└── requirements.txt       # Python dependencies
```

## Environment Variables

Create a `.env` file in the project root (see `SETUP.md` for details).

### Production
```bash
DATABASE_URL=postgresql://user:pass@host:port/dbname
REDIS_URL=redis://user:pass@host:port
FLASK_SECRET_KEY=your-secret-key
INVITE_CODE_PEPPER=separate-random-invite-pepper
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=scrypt:...
ADMIN_EMAIL=admin@example.com
# Emergency only: allows hosted init-db to seed/promote an admin account.
# Prefer tools/create_admin_code.py with an advance-admin invite instead.
# EXIS_ENABLE_ADMIN_BOOTSTRAP=1
```

### Development
```bash
# No DATABASE_URL = uses SQLite
FLASK_SECRET_KEY=dev-secret-key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=dev-password
```

**Note:** The database connection is automatically configured from `DATABASE_URL`. If not set, the app uses SQLite for local development.

Production startup fails closed when `FLASK_SECRET_KEY`/`APP_SECRET_KEY`, `INVITE_CODE_PEPPER`, or `REDIS_URL` are missing. Use `ADMIN_PASSWORD_HASH` in production; plaintext `ADMIN_PASSWORD` is development-only. Hosted production also skips default-admin seeding and automatic `advance-admin` promotion unless `EXIS_ENABLE_ADMIN_BOOTSTRAP=1` is set for an intentional one-time bootstrap.

## Documentation (Merged)

The separate `docs/` folder has been merged into this README. Key operational notes:

### Troubleshooting (NULL title_id)
- Check issues:
  ```bash
  python -m database.tools.db_tools check
  ```
- Fix NULL title_id rows:
  ```bash
  python -m database.tools.db_tools purge-null
  # or
  python -m database.tools.db_tools dedupe --fix-null-inventory
  ```

### Migrations / Schema Changes
- Quantity tracking has been removed (no qty columns on inventory).
- Production web startup should not mutate schema/data. Run this explicit step before deploy/start when schema or bootstrap data changes:
  ```bash
  python -m database.tools.db_tools init-db --no-sync-csv
  ```
- `Procfile` sets `EXIS_AUTO_INIT=0` for the web process. If a host does not run the `release:` command automatically, configure the same `init-db` command as the platform pre-deploy step.
- `EXIS_REQUEST_SCHEMA_CHECK=1` can temporarily re-enable request-time schema checks for emergency maintenance, but it should stay disabled in production.

### Cleanup Notes
- Logs are temporary and safe to delete:
  ```bash
  rm database/logs/*.txt
  ```
- Reports are regenerated as needed:
  ```bash
  rm database/tools/reports/*.tsv
  ```
- Backups live in `database/backups/` (keep as needed).
- In-app `BackupArchive` records are convenience snapshots, not disaster recovery backups. Use Render Postgres PITR/logical backups or an external object-store dump job for durable recovery.

### Dependency Files
- `requirements.txt` is the production/runtime dependency contract used by CI and Render.
- `requirements-dev.txt` adds test and security tooling.
- `requirements-tools.txt` adds optional one-off import/helper dependencies.
- `psycopg2` is built from source for production hygiene; local installs need `pg_config`/Postgres client headers, and CI installs `libpq-dev` before `pip install`.

### Database Structure (Summary)
Core tables:
- `cabinet` (display/reserve locations)
- `book_title` (title metadata)
- `inventory` (title ↔ cabinet, no quantity)
- `admin_user`, `admin_invite`
- `audit_log`
- `event_schedule` + `event_books`

## Deployment

Deployed on Render with:
- PostgreSQL database
- Gunicorn WSGI server
- Automatic backups
- Pre-deploy command: `python -m database.tools.db_tools init-db --no-sync-csv`
- Start command: `EXIS_AUTO_INIT=0 gunicorn app:app`

## Testing / CI

The test kit is intentionally quiet and precise:

```bash
pytest
```

CI runs:
- Python compile checks for the main app/routes/tools
- `pytest` with `-q --tb=short` from `pytest.ini`
- `pip-audit -r requirements.txt --progress-spinner off`
