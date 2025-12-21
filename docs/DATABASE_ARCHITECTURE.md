# Database Architecture Documentation

## Overview

This document describes the database architecture for the Book Inventory System (書展庫存系統). The system has been refactored to use a **single source of truth** approach with PostgreSQL as the primary database.

## Architecture Principles

### 1. Single Source of Truth
- **Production**: PostgreSQL on Render (`DATABASE_URL`) is the **only** source of truth
- **Development**: SQLite (`database/inventory.db`) can be used for local testing
- **CSV**: Used **only** for one-time imports/exports, not for regular operations

### 2. Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    PRODUCTION (Render)                       │
│                                                               │
│  PostgreSQL (DATABASE_URL)                                    │
│         ↑                                                    │
│         │ (read/write)                                       │
│         │                                                    │
│    Flask Application                                          │
│    (app.py)                                                  │
│                                                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                  DEVELOPMENT (Local)                         │
│                                                               │
│  SQLite (database/inventory.db)                             │
│         ↑                                                    │
│         │ (read/write)                                       │
│         │                                                    │
│    Flask Application                                          │
│    (app.py)                                                  │
│                                                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              ONE-TIME IMPORT/EXPORT                         │
│                                                               │
│  CSV (database/inventory.csv)                               │
│    ↕                                                         │
│  Database Tools (database/tools/)                           │
│    ↕                                                         │
│  PostgreSQL / SQLite                                         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 3. Database Schema

The system uses a normalized relational database structure:

#### Core Tables

1. **`cabinet`** - Storage locations (display/reserve cabinets)
   - `id` (PK)
   - `name` (unique)
   - `type` (display/reserve)

2. **`book_title`** - Book metadata (normalized titles)
   - `id` (PK)
   - `title` (unique)
   - `author`
   - `topics` (JSON)
   - `cover_link`
   - `created_at`, `updated_at`

3. **`inventory`** - Stock levels per cabinet
   - `id` (PK)
   - `title_id` (FK → book_title.id)
   - `cabinet_id` (FK → cabinet.id)
   - (quantity tracking removed - no qty_on_hand or qty_reserved columns)
   - `created_at`, `updated_at`
   - Unique constraint: (title_id, cabinet_id)

4. **`admin_user`** - Admin accounts
   - `id` (PK)
   - `username` (unique)
   - `email` (unique)
   - `password_hash`
   - `role`

5. **`admin_invite`** - Invitation codes
   - `id` (PK)
   - `code` (unique)
   - `memo`
   - `created_at`, `used_at`

6. **`audit_log`** - Activity tracking
   - `id` (PK)
   - `actor`
   - `action`
   - `target`
   - `details`
   - `created_at`

7. **`view_event`** - Book view analytics
   - `id` (PK)
   - `title`
   - `source`
   - `actor`
   - `created_at`

## Environment Configuration

### Production (Render)
```bash
DATABASE_URL=postgresql://user:pass@host:port/dbname
FLASK_SECRET_KEY=your-secret-key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-password
ADMIN_EMAIL=admin@example.com
```

### Development (Local)
```bash
# No DATABASE_URL = uses SQLite
FLASK_SECRET_KEY=dev-secret-key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=dev-password
```

## Database Migrations

The system uses **Alembic** for database schema migrations.

### Initial Setup
```bash
# Initialize Alembic (one-time)
flask db init

# Create initial migration
flask db migrate -m "Initial schema"

# Apply migrations
flask db upgrade
```

### Adding New Migrations
```bash
# After modifying models.py
flask db migrate -m "Description of changes"
flask db upgrade
```

### Migration Commands
- `flask db migrate` - Create a new migration from model changes
- `flask db upgrade` - Apply pending migrations
- `flask db downgrade` - Rollback one migration
- `flask db current` - Show current migration version
- `flask db history` - Show migration history

## Data Operations

### Application Code
All database operations go through SQLAlchemy ORM:
- **Read**: Direct queries via `db.session.query()`
- **Write**: Create/update/delete via `db.session.add()`, `db.session.commit()`

### CSV Import/Export (One-Time Only)

#### Import CSV to Database
```bash
# Development (SQLite)
python -m database.tools.db_tools sync-csv

# Production (PostgreSQL) - requires explicit flag
ENABLE_CSV_SYNC=1 python -m database.tools.db_tools sync-csv
```

#### Export Database to CSV
```bash
python -m database.tools.db_tools export-csv
```

**Note**: CSV operations are **disabled by default** in production to prevent accidental data loss.

### Database Sync (Local ↔ Production)

#### Upload Local SQLite to Production
```bash
python database/tools/local_db_upload.py
```

#### Download Production to Local
```bash
python database/tools/cloud_db_download.py --output database/backups/render_dump.sql
```

## Backup Strategy

### Automatic Backups
- **Production**: Hourly automatic backups via `ensure_hourly_backup()`
- **Location**: `database/backups/`
- **Format**: 
  - PostgreSQL: SQL dumps (`.sql`)
  - SQLite: File copies (`.db`)
  - CSV: File copies (`.csv`)

### Manual Backups
```bash
# Via admin dashboard
POST /admin/backup

# Or via script
python -c "from app import app, create_backup; app.app_context().push(); create_backup()"
```

## Data Quality Maintenance

### Health Checks
```bash
# Check for issues (duplicates, missing metadata, etc.)
python -m database.tools.db_tools check

# Diagnose both local and cloud
python database/tools/local_db_sync.py diagnose
```

### Common Fixes
```bash
# Remove duplicate titles
python -m database.tools.db_tools dedupe

# Remove NULL rows
python -m database.tools.db_tools purge-null

# Drop titles from a list
python -m database.tools.db_tools drop-missing --file database/tools/book_csv_missing.txt
```

## Code Organization

### Database Layer
- **`database/models.py`** - SQLAlchemy models
- **`database/tools/`** - Maintenance scripts

### Application Layer
- **`app.py`** - Flask routes and business logic
- **No CSV exports in production code paths**
- **All writes go directly to database**

### Service Layer (Future)
- **`database/services.py`** - Centralized data operations (optional)

## Migration from Old System

### If You Have Existing CSV Data

1. **Backup everything first**
   ```bash
   cp database/inventory.csv database/backups/inventory_backup.csv
   ```

2. **Import CSV to local SQLite** (for testing)
   ```bash
   python -m database.tools.db_tools sync-csv
   ```

3. **Verify data quality**
   ```bash
   python -m database.tools.db_tools check
   ```

4. **Fix any issues**
   ```bash
   python -m database.tools.db_tools dedupe
   python -m database.tools.db_tools purge-null
   ```

5. **Upload to production**
   ```bash
   python database/tools/local_db_upload.py
   ```

6. **Verify production**
   ```bash
   python database/tools/local_db_sync.py diagnose --cloud-only
   ```

### After Migration

- ✅ **Remove CSV exports** from production code (already done)
- ✅ **Use PostgreSQL** as single source of truth
- ✅ **CSV is optional** - only for one-time imports/exports

## Best Practices

1. **Never export to CSV in production** - Database is the source of truth
2. **Always backup before migrations** - Use `create_backup()`
3. **Run health checks regularly** - `python -m database.tools.db_tools check`
4. **Use migrations for schema changes** - Never modify tables directly
5. **Test locally first** - Use SQLite for development, PostgreSQL for production

## Troubleshooting

### Issue: CSV sync disabled in production
**Solution**: This is intentional. Use database tools directly:
```bash
ENABLE_CSV_SYNC=1 python -m database.tools.db_tools sync-csv
```

### Issue: Migration conflicts
**Solution**: 
```bash
flask db current  # Check current version
flask db history  # See migration history
flask db downgrade  # Rollback if needed
```

### Issue: Data inconsistencies
**Solution**:
```bash
python -m database.tools.db_tools check  # Identify issues
python -m database.tools.db_tools dedupe  # Fix duplicates
python -m database.tools.db_tools purge-null  # Remove NULL rows
```

## Summary

- ✅ **PostgreSQL is the single source of truth in production**
- ✅ **CSV is only for one-time imports/exports**
- ✅ **No automatic CSV sync in production**
- ✅ **All writes go directly to database**
- ✅ **Proper migrations with Alembic**
- ✅ **Clear separation of concerns**

