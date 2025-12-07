import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, text


# ---------- CONFIG ----------

# Path to your local SQLite DB (defaults to repo_root/database/inventory.db)
REPO_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = Path(os.environ.get("SQLITE_PATH") or REPO_ROOT / "database" / "inventory.db")

# Postgres URL from Render (set via env var)
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Please set DATABASE_URL to your Render PostgreSQL URL before running this script.")

# Render sometiSELECT * FROM book_titlemes gives postgres://, SQLAlchemy prefers postgresql+psycopg2://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)

# Tables to migrate (order matters for FK relationships)
TABLES = [
    "admin_user",
    "admin_invite",
    "cabinet",
    "book_title",
    "inventory",
    "view_event",
    "audit_log",
]


# ---------- HELPER FUNCTIONS ----------

def connect_sqlite(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"SQLite DB not found at {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def connect_postgres(url: str):
    engine = create_engine(url, future=True)
    return engine


def truncate_tables(engine, tables):
    """Optional: clear Postgres tables before inserting, to avoid duplicate IDs."""
    print("Truncating Postgres tables (if any rows exist)...")
    with engine.begin() as conn:
        for table in tables:
            try:
                print(f"  TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")
                conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))
            except Exception as exc:
                # Some managed Postgres (e.g., limited roles) disallow replication_role changes.
                # Fallback: delete all rows and reset identity.
                print(f"    truncate failed for {table} ({exc}); falling back to DELETE/IDENTITY reset.")
                conn.execute(text(f"DELETE FROM {table};"))
                conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 1, false);"))
    print("Truncate done.\n")


def migrate_table(sqlite_conn, pg_engine, table_name: str):
    print(f"=== Migrating table: {table_name} ===")
    try:
        rows = sqlite_conn.execute(f"SELECT * FROM {table_name}").fetchall()
    except sqlite3.OperationalError as exc:
        print(f"  Skip: table '{table_name}' not found in SQLite ({exc}).\n")
        return
    if not rows:
        print(f"  No rows in SQLite.{os.linesep}")
        return

    cols = rows[0].keys()
    col_list = ", ".join(f'"{c}"' for c in cols)  # quote column names
    placeholders = ", ".join(f":{c}" for c in cols)
    insert_sql = text(f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})")

    print(f"  Found {len(rows)} rows in SQLite.")
    inserted = 0
    with pg_engine.begin() as conn:
        for row in rows:
            conn.execute(insert_sql, dict(row))
            inserted += 1

    print(f"  Inserted {inserted} rows into Postgres.{os.linesep}")


def fix_sequences(pg_engine):
    """
    Ensure Postgres sequences are set to MAX(id) for each table,
    so future inserts don't try to reuse existing IDs.
    """
    print("Fixing ID sequences in Postgres...")

    sequence_map = {
        "admin_user": "admin_user_id_seq",
        "admin_invite": "admin_invite_id_seq",
        "cabinet": "cabinet_id_seq",
        "book_title": "book_title_id_seq",
        "inventory": "inventory_id_seq",
        "view_event": "view_event_id_seq",
        "audit_log": "audit_log_id_seq",
    }

    with pg_engine.begin() as conn:
        for table, seq in sequence_map.items():
            print(f"  Adjusting sequence {seq} for table {table}...")
            conn.execute(text(f"""
                SELECT setval(
                    '{seq}',
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    true
                );
            """))

    print("Sequence fix complete.\n")


def main():
    print(f"Using SQLite: {SQLITE_PATH}")
    print(f"Using Postgres: {DATABASE_URL}\n")

    sqlite_conn = connect_sqlite(SQLITE_PATH)
    pg_engine = connect_postgres(DATABASE_URL)

    # 1) Optional: clear Postgres tables first (recommended if DB is new / test data only)
    truncate_tables(pg_engine, TABLES)

    # 2) Migrate each table
    for table in TABLES:
        migrate_table(sqlite_conn, pg_engine, table)

    # 3) Fix sequences so future inserts auto-increment correctly
    fix_sequences(pg_engine)

    print("✅ Migration complete! You can now check your Postgres DB.")


if __name__ == "__main__":
    main()
