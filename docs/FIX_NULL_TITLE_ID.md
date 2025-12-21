# Fixing NULL title_id Issues

## Problem

You're encountering this error:
```
[dedupe][error] database constraint failed: null value in column "title_id" of relation "inventory" violates not-null constraint
```

This means there are inventory rows with `title_id = NULL`, which violates the database constraint.

## Quick Fix

Run this command to delete all inventory rows with NULL title_id:

```bash
python -m database.tools.db_tools purge-null
```

Or use the dedupe command with the fix flag:

```bash
python -m database.tools.db_tools dedupe --fix-null-inventory
```

## Step-by-Step Solution

### Step 1: Check the Issue

```bash
python -m database.tools.db_tools check
```

This will show you:
- How many inventory rows have NULL title_id
- Details about each problematic row

### Step 2: Fix the Issue

**Option A: Use purge-null (Recommended)**
```bash
# Dry run first to see what will be deleted
python -m database.tools.db_tools purge-null --dry-run

# Actually delete the NULL rows
python -m database.tools.db_tools purge-null
```

**Option B: Use dedupe with fix flag**
```bash
python -m database.tools.db_tools dedupe --fix-null-inventory
```

### Step 3: Verify Fix

```bash
# Check again - should show no NULL title_id issues
python -m database.tools.db_tools check
```

### Step 4: Run Dedupe

Now you can run dedupe without errors:

```bash
python -m database.tools.db_tools dedupe
```

## Why This Happens

NULL title_id in inventory can occur due to:
1. Data migration issues
2. Manual database edits
3. Bugs in previous code versions
4. Foreign key constraint violations

## Prevention

The updated code now:
- ✅ Detects NULL title_id issues in `check` command
- ✅ Automatically fixes them in `dedupe` with `--fix-null-inventory` flag
- ✅ `purge-null` properly handles NULL title_id

## What Gets Deleted

When you run `purge-null`, it will delete:
- All inventory rows where `title_id IS NULL`
- All inventory rows where `cabinet_id IS NULL`
- (quantity tracking removed - no qty_on_hand or qty_reserved columns)
- All book_title rows with NULL in required fields

**Note**: These are invalid data rows that cannot be used anyway, so deleting them is safe.

## Manual SQL Fix (Advanced)

If you need more control, you can run SQL directly:

```sql
-- Check how many rows
SELECT COUNT(*) FROM inventory WHERE title_id IS NULL;

-- Delete them
DELETE FROM inventory WHERE title_id IS NULL;
```

But using the `purge-null` command is safer and provides better reporting.

