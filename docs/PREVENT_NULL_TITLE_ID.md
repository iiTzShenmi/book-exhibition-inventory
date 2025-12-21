# Preventing NULL title_id Errors

## Problem
The error `null value in column "title_id" of relation "inventory" violates not-null constraint` was occurring repeatedly, causing dedupe operations to fail.

## Solution: Multi-Layer Protection

We've implemented **multiple layers of protection** to prevent NULL `title_id` values from being created:

### 1. Model-Level Validation (`database/models.py`)

**SQLAlchemy Validators:**
- `@validates("title_id")` - Raises `ValueError` if `title_id` is `None` or invalid
- `@validates("cabinet_id")` - Raises `ValueError` if `cabinet_id` is `None` or invalid

**Event Listeners:**
- `before_insert` and `before_update` event listeners catch any Inventory objects with NULL values before they reach the database
- Provides detailed error messages with object IDs for debugging

### 2. Service Layer Validation (`database/services.py`)

**Defensive Checks in `create_or_update_inventory()`:**
- Validates `title_id` and `cabinet_id` are positive integers
- Verifies that the `BookTitle` and `Cabinet` actually exist before creating Inventory
- Raises clear `ValueError` messages if validation fails

### 3. Application-Level Checks (`app.py`)

**Defensive Checks in Critical Paths:**
- `sync_csv_to_db()` - Validates `title_obj.id` before creating Inventory
- `add_book()` route - Validates `title_obj.id` and `cabinet_id` before creating Inventory
- `migrate_legacy_books_into_inventory()` - Validates IDs before creating Inventory
- `toggle_cabinet_book()` - Validates IDs before creating Inventory

### 4. Database Tools Protection (`database/tools/db_tools.py`)

**Defensive Checks in `merge_inventories()`:**
- Checks for NULL `title_id` and `cabinet_id` before merging
- Automatically deletes invalid inventory rows with warnings
- Validates `keeper.id` before updating `title_id`

## How It Works

1. **First Line of Defense:** SQLAlchemy validators catch NULL values when attributes are set
2. **Second Line of Defense:** Event listeners catch any objects before database flush
3. **Third Line of Defense:** Application-level checks validate data before creating objects
4. **Fourth Line of Defense:** Service layer validates foreign key existence
5. **Database Constraint:** PostgreSQL `NOT NULL` constraint (final safety net)

## Error Messages

If a NULL `title_id` is attempted, you'll see clear error messages:

```
ValueError: title_id cannot be NULL. This violates database constraints. 
Ensure a valid BookTitle exists before creating Inventory.
```

Or from event listeners:
```
ValueError: Inventory object (id=881) has NULL title_id. 
This will cause a database constraint violation. 
Check the code that created this Inventory object.
```

## Testing

To verify the protection is working:

```python
# This should raise ValueError immediately
inventory = Inventory(title_id=None, cabinet_id=1)
# ValueError: title_id cannot be NULL...
```

## Existing NULL Values

If you still have NULL values in your database:

1. **Run purge-null:**
   ```bash
   python -m database.tools.db_tools purge-null
   ```

2. **Or use dedupe with fix flag:**
   ```bash
   python -m database.tools.db_tools dedupe --fix-null-inventory
   ```

## Prevention

With these protections in place, NULL `title_id` values should **never** be created again. The multiple layers ensure that even if one check is bypassed, others will catch it.

