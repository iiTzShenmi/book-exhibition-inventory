# Troubleshooting Guide

## Common Issues and Solutions

### Issue: `purge-null` says "no NULL-containing rows found" but `dedupe` finds NULL title_id

**Problem**: The `purge-null` command wasn't detecting NULL `title_id` rows correctly.

**Solution**: Fixed in latest version. The command now:
- Uses ORM `.count()` to detect NULL rows (same as `dedupe`)
- Always deletes if count > 0, even if SQL SELECT doesn't return rows
- Uses raw SQL DELETE to remove NULL rows

**How to fix**:
```bash
# This should now work correctly
python -m database.tools.db_tools purge-null

# Then run dedupe
python -m database.tools.db_tools dedupe
```

### Issue: `dedupe` fails with "null value in column title_id violates not-null constraint"

**Problem**: There are inventory rows with NULL `title_id` that need to be deleted first.

**Solution**:
```bash
# Option 1: Use purge-null first
python -m database.tools.db_tools purge-null
python -m database.tools.db_tools dedupe

# Option 2: Use dedupe with fix flag
python -m database.tools.db_tools dedupe --fix-null-inventory
```

### Issue: `check` doesn't report NULL title_id issues

**Problem**: The check command might not be detecting NULL values correctly.

**Solution**: Fixed in latest version. The `check` command now:
- Uses ORM `.count()` to detect NULL `title_id`
- Uses raw SQL to get details when ORM `.all()` fails
- Properly reports NULL issues

**Verify**:
```bash
python -m database.tools.db_tools check
```

Should now show:
```
[issue] inventory rows with NULL title_id (CRITICAL): X
```

## Debugging Steps

### 1. Check for NULL title_id
```bash
python -m database.tools.db_tools check
```

### 2. Verify with direct SQL (if you have psql access)
```sql
SELECT COUNT(*) FROM inventory WHERE title_id IS NULL;
SELECT id, title_id, cabinet_id FROM inventory WHERE title_id IS NULL;
```

### 3. Fix the issue
```bash
python -m database.tools.db_tools purge-null
```

### 4. Verify fix
```bash
python -m database.tools.db_tools check
python -m database.tools.db_tools dedupe
```

## Why This Happens

NULL `title_id` in inventory can occur due to:
1. **Data migration issues** - During CSV/DB sync
2. **Manual database edits** - Direct SQL modifications
3. **Bugs in previous code** - Foreign key constraint violations
4. **Race conditions** - Concurrent operations

## Prevention

The updated code now:
- ✅ Properly detects NULL values using ORM count
- ✅ Uses raw SQL DELETE to remove NULL rows
- ✅ Reports NULL issues in `check` command
- ✅ Auto-fixes in `dedupe` with `--fix-null-inventory` flag

## Still Having Issues?

If `purge-null` still doesn't work:

1. **Check the actual database**:
   ```bash
   # Connect to your database and run:
   SELECT id, title_id, cabinet_id FROM inventory WHERE title_id IS NULL;
   ```

2. **Manual fix** (if needed):
   ```sql
   DELETE FROM inventory WHERE title_id IS NULL;
   ```

3. **Verify**:
   ```bash
   python -m database.tools.db_tools check
   ```


