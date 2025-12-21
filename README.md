# 書展庫存系統 (Book Inventory System)

Flask-based inventory management system for book exhibitions. This single README collects the runbook, database notes, helper tools, and prototypes that were previously scattered across multiple files.

## Quick Start

### Production (Render)
- PostgreSQL via `DATABASE_URL` is the single source of truth.
- CSV is only for one-time imports/exports.

### Development (Local)
- SQLite at `database/inventory.db`.
- CSV sync is available for testing and local imports.

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

## Prototypes

- **Top Views Monitor** (`prototypes/top_views_monitor/`): Logs views as JSONL (`logs/view_events.jsonl`) and reports most-viewed titles.
  - Run: `python prototypes/top_views_monitor/analyze_views.py --days 7 --top 15`
  - Optional filters: `--days 1 --source search --top 10`
  - Log format: `{"timestamp":"2025-02-20T12:30:45","title":"原子習慣","source":"search","actor":"guest"}`
  - No external deps; safe to run even if the log file is missing.

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
├── docs/                  # Documentation
└── requirements.txt       # Python dependencies
```

## Environment Variables

Create a `.env` file in the project root (see `SETUP.md` for details).

### Production
```bash
DATABASE_URL=postgresql://user:pass@host:port/dbname
FLASK_SECRET_KEY=your-secret-key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-password
ADMIN_EMAIL=admin@example.com
```

### Development
```bash
# No DATABASE_URL = uses SQLite
FLASK_SECRET_KEY=dev-secret-key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=dev-password
```

**Note:** The database connection is automatically configured from `DATABASE_URL`. If not set, the app uses SQLite for local development.

## Documentation Index

- **[Quick Guide](QUICK_GUIDE.md)** - Common tasks and shortcuts
- **[Setup Guide](SETUP.md)** - Initial setup and configuration
- **[Database Architecture](docs/DATABASE_ARCHITECTURE.md)** - Schema and best practices
- **[Migration Guide](docs/MIGRATION_GUIDE.md)** - Migration instructions
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** - Common issues and fixes
- **[Fix Null Title ID](docs/FIX_NULL_TITLE_ID.md)** / **[Prevent Null Title ID](docs/PREVENT_NULL_TITLE_ID.md)** / **[Remove Quantity Tracking](docs/REMOVE_QUANTITY_TRACKING.md)**

## Deployment

Deployed on Render with:
- PostgreSQL database
- Gunicorn WSGI server
- Automatic backups

See `docs/MIGRATION_GUIDE.md` for migration instructions.


