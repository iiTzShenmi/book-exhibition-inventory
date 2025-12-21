# Quick Guide

Quick reference for common tasks.

## Prerequisites

Before running these commands:
1. **Activate your virtual environment** (e.g., `conda activate Py3.11.3`)
2. **Set DATABASE_URL** (for PostgreSQL operations):
   ```powershell
   $env:DATABASE_URL = "postgresql://user:pass@host:port/dbname"
   ```
3. **Install dependencies**: `pip install -r requirements.txt`

## Import Data

### Import CSV to PostgreSQL

```powershell
# Windows PowerShell
# Requires: Virtual environment activated, DATABASE_URL set
$env:ENABLE_CSV_SYNC=1
python -m database.tools.db_tools sync-csv
```

### Upload SQLite to PostgreSQL

```powershell
# Simple upload
# Requires: Virtual environment activated, DATABASE_URL set
python database/tools/db_sync.py upload

# Full workflow with auto-fix
python database/tools/db_sync.py push --auto-fix
```

## Fix Duplicates

If you see duplicate detection errors:

```powershell
# Option 1: Auto-fix and upload (recommended)
python database/tools/db_sync.py push --auto-fix

# Option 2: Manual fix
# Step 1: Fix duplicates in SQLite
$env:DATABASE_URL = ""
$env:SKIP_INIT = "1"
python -m database.tools.db_tools dedupe --fix-null-inventory

# Step 2: Restore DATABASE_URL and upload
$env:DATABASE_URL = "your-postgres-url"
python database/tools/db_sync.py upload

# Option 3: Allow duplicates (not recommended)
python database/tools/db_sync.py upload --allow-duplicates
```

## Local Testing

### Restart Flask Server

After making code changes:

```powershell
# Stop server (Ctrl+C), then:
flask run
```

### Test Similarity Function

1. Make sure database has books
2. Search for a book that doesn't exist (e.g., "我被")
3. Check console for debug output:
   ```
   [similarity] Query: '我被', Found X profiles, Got Y suggestions
   ```

## Troubleshooting

### Database Empty

If similarity function shows "No books found":
- Import data: `ENABLE_CSV_SYNC=1 python -m database.tools.db_tools sync-csv`
- Or upload from SQLite: `python database/tools/db_sync.py upload`

### SKIP_INIT Not Working

Make sure to use `SKIP_INIT=0` or leave it unset (not `SKIP_INIT=0` as a string).

### Import Errors

- Check `DATABASE_URL` is set correctly
- For PostgreSQL, use `ENABLE_CSV_SYNC=1` flag
- Check console for specific error messages

### Quantity Column Error (SQLite)

If you see `NOT NULL constraint failed: inventory.qty_on_hand`:

This means your SQLite database still has old quantity columns. The migration will run automatically on next startup, or you can run:

```powershell
# Clear SKIP_INIT to allow migration
$env:SKIP_INIT = ""
python -c "from app import app, initialize_app; app.app_context().push(); initialize_app()"
```

Or simply restart your Flask server without `SKIP_INIT` set - the migration will run automatically.

## See Also

- **[SETUP.md](SETUP.md)** - Initial setup instructions
- **[docs/README_DATABASE.md](docs/README_DATABASE.md)** - Database documentation
- **[MIGRATION_NOTES.md](MIGRATION_NOTES.md)** - Tool migration guide

