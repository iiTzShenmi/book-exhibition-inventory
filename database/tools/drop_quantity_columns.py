#!/usr/bin/env python
"""
Migration script to drop qty_on_hand and qty_reserved columns from inventory table.

This script:
1. Drops qty_on_hand column from inventory table
2. Drops qty_reserved column from inventory table
3. Works with both PostgreSQL and SQLite

Usage:
    python -m database.tools.drop_quantity_columns [--dry-run]
    OR
    python database/tools/drop_quantity_columns.py [--dry-run]
"""

import argparse
import os
import sys
from pathlib import Path

# Add parent directory to path to import app (same pattern as db_tools.py)
SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Load environment variables early (same as local_db_sync.py)
try:
    from tools import env_loader  # noqa: F401
except ImportError:
    pass  # env_loader is optional

try:
    from sqlalchemy import text
except ImportError:
    print("[error] SQLAlchemy not found. Please activate your virtual environment.")
    print("  For conda: conda activate Py3.11.3")
    print("  For venv: source venv/bin/activate (Linux/Mac) or venv\\Scripts\\activate (Windows)")
    sys.exit(1)

try:
    from app import app, db, is_postgres  # noqa: E402
except ImportError as e:
    print(f"[error] Failed to import app: {e}")
    print("  Make sure you're running from the project root directory.")
    sys.exit(1)


def drop_quantity_columns(dry_run: bool = False) -> int:
    """Drop qty_on_hand and qty_reserved columns from inventory table."""
    with app.app_context():
        if is_postgres():
            # PostgreSQL
            print("[drop-quantity-columns] Using PostgreSQL")
            
            # Check if columns exist
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'inventory' 
                AND column_name IN ('qty_on_hand', 'qty_reserved')
            """)
            existing_columns = [row[0] for row in db.session.execute(check_query).fetchall()]
            
            if not existing_columns:
                print("[drop-quantity-columns] Columns qty_on_hand and qty_reserved do not exist. Nothing to do.")
                return 0
            
            print(f"[drop-quantity-columns] Found columns to drop: {existing_columns}")
            
            if dry_run:
                print("[drop-quantity-columns] DRY RUN - would drop columns:", existing_columns)
                return 0
            
            # Drop columns
            for col in existing_columns:
                try:
                    drop_query = text(f"ALTER TABLE inventory DROP COLUMN IF EXISTS {col}")
                    db.session.execute(drop_query)
                    print(f"[drop-quantity-columns] Dropped column: {col}")
                except Exception as e:
                    print(f"[drop-quantity-columns][error] Failed to drop column {col}: {e}")
                    db.session.rollback()
                    return 1
            
            db.session.commit()
            print("[drop-quantity-columns] Successfully dropped quantity columns from inventory table.")
            
        else:
            # SQLite - doesn't support DROP COLUMN directly, need to recreate table
            print("[drop-quantity-columns] Using SQLite")
            print("[drop-quantity-columns] SQLite doesn't support DROP COLUMN directly.")
            print("[drop-quantity-columns] You'll need to:")
            print("  1. Export your data")
            print("  2. Recreate the table without quantity columns")
            print("  3. Import the data back")
            print("[drop-quantity-columns] Or use a tool like sqlite3 to manually recreate the table.")
            return 1
    
    return 0


def main():
    parser = argparse.ArgumentParser(description="Drop qty_on_hand and qty_reserved columns from inventory table")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()
    
    return drop_quantity_columns(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

