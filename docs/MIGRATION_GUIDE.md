# Migration Guide: From CSV/DB Hybrid to PostgreSQL-Only

This guide helps you migrate from the old CSV/DB hybrid system to the new PostgreSQL-only architecture.

## Overview of Changes

### Before (Old System)
- CSV file (`database/inventory.csv`) was synced with database
- Every database change automatically exported to CSV
- SQLite used for local development
- PostgreSQL used for production
- Complex sync logic between CSV ↔ SQLite ↔ PostgreSQL

### After (New System)
- **PostgreSQL is the single source of truth in production**
- CSV is **only** for one-time imports/exports
- SQLite is **only** for local development
- **No automatic CSV exports** in production
- All writes go directly to database

## Step-by-Step Migration

### Step 1: Backup Everything

```bash
# Backup your current database
python database/tools/cloud_db_download.py --output database/backups/pre_migration_backup.sql

# Backup CSV (if you want to keep it)
cp database/inventory.csv database/backups/inventory_backup.csv
```

### Step 2: Verify Current Data

```bash
# Check for data quality issues
python -m database.tools.db_tools check

# Fix any issues found
python -m database.tools.db_tools dedupe
python -m database.tools.db_tools purge-null
```

### Step 3: Deploy Updated Code

The new code has been refactored to:
- ✅ Remove automatic CSV exports from all route handlers
- ✅ Make CSV export optional (only when `ENABLE_CSV_EXPORT=1`)
- ✅ Use PostgreSQL as single source of truth

**No code changes needed** - just deploy the updated `app.py`.

### Step 4: Verify Production

After deploying:

1. **Test a few operations** (add book, update quantity, etc.)
2. **Verify data persists** in PostgreSQL
3. **Check that CSV is NOT being updated** (unless `ENABLE_CSV_EXPORT=1`)

### Step 5: Optional - One-Time CSV Export

If you need a CSV export for backup/archive:

```bash
# Set environment variable to enable export
export ENABLE_CSV_EXPORT=1

# Run export (one-time)
python -c "from app import app, export_db_to_csv; app.app_context().push(); export_db_to_csv()"

# Unset the variable
unset ENABLE_CSV_EXPORT
```

## Environment Variables

### Production (Render)
```bash
DATABASE_URL=postgresql://user:pass@host:port/dbname
FLASK_SECRET_KEY=your-secret-key
# DO NOT set ENABLE_CSV_EXPORT in production (unless for one-time export)
```

### Development (Local)
```bash
# No DATABASE_URL = uses SQLite
FLASK_SECRET_KEY=dev-secret-key
# Optional: ENABLE_CSV_EXPORT=1 for development if you want CSV sync
```

## What Changed in Code

### Removed Automatic CSV Exports

All route handlers that previously called `export_db_to_csv()` have been updated:
- `/toggle/<int:book_id>` - removed CSV export
- `/modify_cabinet/<string:title>` - removed CSV export
- `/cabinets` (POST/PATCH/DELETE) - removed CSV export
- `/cabinets/<id>/books/*` - removed CSV export
- `/add_book` - removed CSV export
- All other write operations - removed CSV export

### Updated Functions

1. **`export_db_to_csv()`** - Now skips in production unless `ENABLE_CSV_EXPORT=1`
2. **`create_backup()`** - CSV backup is optional (only if enabled)
3. **`sync_csv_to_db()`** - Already had production guard (unchanged)

## Rollback Plan

If you need to rollback:

1. **Restore from backup**:
   ```bash
   psql $DATABASE_URL < database/backups/pre_migration_backup.sql
   ```

2. **Revert code** (if needed):
   ```bash
   git checkout <previous-commit>
   ```

## Verification Checklist

After migration, verify:

- [ ] Database operations work correctly
- [ ] Data persists in PostgreSQL
- [ ] CSV file is NOT being updated automatically
- [ ] Backups work correctly
- [ ] Admin dashboard functions normally
- [ ] Search functionality works
- [ ] All CRUD operations work

## Common Questions

### Q: Will my existing CSV data be lost?
**A:** No. CSV file remains untouched. It's just not automatically synced anymore.

### Q: Can I still import from CSV?
**A:** Yes, use:
```bash
ENABLE_CSV_SYNC=1 python -m database.tools.db_tools sync-csv
```

### Q: Can I still export to CSV?
**A:** Yes, use:
```bash
ENABLE_CSV_EXPORT=1 python -c "from app import app, export_db_to_csv; app.app_context().push(); export_db_to_csv()"
```

### Q: What if I need CSV sync in production?
**A:** Set `ENABLE_CSV_EXPORT=1` and `ENABLE_CSV_SYNC=1` in your environment, but this is **not recommended** as it defeats the purpose of having a single source of truth.

### Q: Will this break my existing deployment?
**A:** No. The changes are backward compatible. Existing data and functionality remain intact.

## Support

If you encounter issues:

1. Check `DATABASE_ARCHITECTURE.md` for architecture details
2. Review logs for errors
3. Run health checks: `python -m database.tools.db_tools check`
4. Verify database connection: Check `DATABASE_URL` is set correctly

## Summary

✅ **PostgreSQL is now the single source of truth**
✅ **CSV exports removed from production code paths**
✅ **All writes go directly to database**
✅ **Backward compatible - no data loss**
✅ **CSV still available for one-time imports/exports**

