# Remove Quantity Tracking

This document describes the changes made to remove `qty_on_hand` and `qty_reserved` columns from the inventory system.

## Summary

Quantity tracking has been removed from the inventory system. The `inventory` table now only tracks which books are in which cabinets, without tracking specific quantities.

## Changes Made

### 1. Database Model (`database/models.py`)
- Removed `qty_on_hand` and `qty_reserved` columns from `Inventory` model
- Updated `in_stock` property to always return `True` (presence in inventory means in stock)

### 2. Application Code (`app.py`)
- Removed all quantity-based filtering and calculations
- Updated toggle functions to delete/add inventory records instead of toggling quantity
- Updated CSV export to use `in_stock` boolean instead of quantity
- Simplified stock status logic (all inventory records represent books in stock)

### 3. Service Layer (`database/services.py`)
- Removed `qty_on_hand` and `qty_reserved` parameters from `create_or_update_inventory()`
- Updated `adjust_quantity()` to delete inventory if delta is negative, otherwise update timestamp

### 4. Database Tools (`database/tools/db_tools.py`)
- Removed quantity-based checks and validations
- Removed quantity columns from SQL queries
- Updated merge logic to not merge quantities

### 5. Frontend (`static/js/admin_dashboard.js`)
- Updated to use `in_stock` boolean instead of `qty_on_hand`

### 6. Documentation
- Updated `docs/DATABASE_ARCHITECTURE.md` to reflect removed columns
- Updated `docs/FIX_NULL_TITLE_ID.md` to remove quantity-related references

## Migration Script

A migration script has been created at `database/tools/drop_quantity_columns.py` to drop the columns from the database.

### Usage

**For PostgreSQL (Render):**
```bash
# Dry run first to see what will be done
python database/tools/drop_quantity_columns.py --dry-run

# Actually drop the columns
python database/tools/drop_quantity_columns.py
```

**For SQLite:**
SQLite doesn't support `DROP COLUMN` directly. You'll need to:
1. Export your data
2. Recreate the table without quantity columns
3. Import the data back

Or manually recreate the table using sqlite3.

## Behavior Changes

### Before
- Inventory records tracked specific quantities
- `qty_on_hand` and `qty_reserved` were used for stock management
- Toggle functions changed quantity between 0 and 1
- Filtering could show "out of stock" items

### After
- Inventory records only indicate presence (book exists in cabinet)
- All inventory records represent books that are "in stock"
- Toggle functions delete/add inventory records
- No "out of stock" filtering (all inventory is in stock)

## Testing

After running the migration:
1. Verify the columns are dropped: `python -m database.tools.db_tools check`
2. Test adding/removing books from cabinets
3. Verify CSV export works correctly
4. Check that admin dashboard displays correctly

## Notes

- The `in_stock` property now always returns `True` for any inventory record
- Quantity-based alerts and replenishment logic have been disabled
- All inventory operations now work on a presence/absence basis

