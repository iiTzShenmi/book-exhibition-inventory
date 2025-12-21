# Migration Notes: Unified db_sync.py

## What Changed

The database sync tools have been unified into a single file: `database/tools/db_sync.py`

### Old Files (Deprecated)
- ❌ `database/tools/local_db_upload.py` - **DO NOT USE DIRECTLY**
- ❌ `database/tools/local_db_sync.py` - **DO NOT USE DIRECTLY**

### New File (Use This)
- ✅ `database/tools/db_sync.py` - **USE THIS INSTEAD**

## Command Mapping

### Old → New

| Old Command | New Command |
|------------|-------------|
| `python database/tools/local_db_upload.py` | `python database/tools/db_sync.py upload` |
| `python database/tools/local_db_upload.py --allow-duplicates` | `python database/tools/db_sync.py upload --allow-duplicates` |
| `python database/tools/local_db_sync.py push` | `python database/tools/db_sync.py push` |
| `python database/tools/local_db_sync.py push --auto-fix` | `python database/tools/db_sync.py push --auto-fix` |
| `python database/tools/local_db_sync.py diagnose` | `python database/tools/db_sync.py diagnose` |

## New Commands Available

The unified tool also provides a `clean` command:

```powershell
# Clean local database
python database/tools/db_sync.py clean --purge-null --dedupe --auto-fix
```

## Why the Change?

1. **Simpler**: One file instead of two
2. **Cleaner**: Direct function calls instead of subprocess calls
3. **Easier to maintain**: All sync logic in one place
4. **More features**: Additional commands like `clean`

## Migration Steps

1. Update any scripts that call the old files
2. Update documentation references
3. The old files will show deprecation warnings but still work (for now)

## Questions?

If you have issues, check:
- `FIX_DUPLICATES.md` - Updated with new commands
- `database/README.md` - Updated tool list
- `database/tools/db_sync.py` - Main file with all functionality

