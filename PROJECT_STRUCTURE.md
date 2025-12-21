# Project Structure

## Directory Layout

```
Web/
├── app.py                      # Main Flask application
├── recommender.py              # Book recommendation engine
├── requirements.txt            # Python dependencies
├── runtime.txt                 # Python runtime version
├── Procfile                    # Render deployment config
├── Makefile                    # Common commands
├── README.md                   # Project overview
│
├── database/                   # Database layer
│   ├── __init__.py
│   ├── models.py              # SQLAlchemy models
│   ├── services.py            # Service layer (data operations)
│   ├── README.md              # Database-specific docs
│   ├── inventory.csv          # CSV import/export (one-time use)
│   ├── inventory.db           # SQLite (local dev only)
│   ├── backups/               # Database backups
│   │   └── .gitkeep
│   └── tools/                 # Database maintenance scripts
│       ├── db_tools.py        # Main maintenance CLI
│       ├── local_db_sync.py    # Sync local SQLite to PostgreSQL
│       ├── cloud_db_download.py
│       ├── local_db_upload.py
│       └── reports/           # Generated reports
│
├── tools/                      # Utility scripts
│   ├── __init__.py
│   ├── env_loader.py          # Environment variable loader
│   ├── watchdog.py            # Health monitoring
│   ├── fetch_cover_url.py     # Cover image fetcher
│   ├── fetch_author.py         # Author fetcher
│   ├── fetch_topics.py         # Topics fetcher
│   └── create_admin_code.py   # Admin invite code generator
│
├── static/                     # Static assets
│   ├── css/
│   │   └── main.css
│   └── js/
│       ├── base.js
│       └── admin_dashboard.js
│
├── templates/                  # Jinja2 templates
│   ├── base.html
│   ├── home.html
│   ├── login.html
│   ├── register.html
│   ├── admin_dashboard.html
│   ├── audit.html
│   ├── search_results.html
│   └── ...
│
├── docs/                       # Documentation
│   ├── README.md              # Documentation index
│   ├── DATABASE_ARCHITECTURE.md
│   ├── MIGRATION_GUIDE.md
│   ├── README_DATABASE.md
│   └── FIX_NULL_TITLE_ID.md
│
└── prototypes/                 # Experimental features
    └── top_views_monitor/
        ├── analyze_views.py
        └── logs/
```

## Key Files

### Application
- **`app.py`** - Main Flask application with all routes
- **`recommender.py`** - Book recommendation system

### Database
- **`database/models.py`** - SQLAlchemy ORM models
- **`database/services.py`** - Service layer for data operations
- **`database/tools/db_tools.py`** - CLI for database maintenance
- **`database/tools/local_db_sync.py`** - Sync local SQLite to PostgreSQL cloud

### Configuration
- **`requirements.txt`** - Python package dependencies
- **`Procfile`** - Render deployment configuration
- **`.gitignore`** - Git ignore rules

### Documentation
- **`README.md`** - Project overview and quick start
- **`docs/`** - All detailed documentation

## File Organization Principles

1. **Separation of Concerns**
   - `database/` - All database-related code
   - `tools/` - Utility scripts
   - `static/` - Frontend assets
   - `templates/` - HTML templates
   - `docs/` - Documentation

2. **Single Source of Truth**
   - Production: PostgreSQL (via `DATABASE_URL`)
   - Development: SQLite (`database/inventory.db`)
   - CSV: One-time import/export only

3. **Clean Root Directory**
   - Only essential files in root
   - Documentation in `docs/`
   - Temporary files ignored by git

## Maintenance

### Database Maintenance
```bash
# Check for issues
python -m database.tools.db_tools check

# Fix duplicates
python -m database.tools.db_tools dedupe

# Remove NULL rows
python -m database.tools.db_tools purge-null
```

### Development
```bash
# Run locally
flask run

# Or with gunicorn
gunicorn app:app
```

## Deployment

- **Platform**: Render
- **Database**: Render PostgreSQL
- **WSGI Server**: Gunicorn
- **Configuration**: `Procfile` + environment variables


