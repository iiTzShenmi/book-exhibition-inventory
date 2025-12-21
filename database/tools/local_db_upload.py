"""
⚠️ DEPRECATED: This file is deprecated. Use `db_sync.py` instead.

Upload data from the local SQLite inventory DB into the Postgres database (Render),
with a preflight duplicate check on book titles.

NEW USAGE (recommended):
  python database/tools/db_sync.py upload
  python database/tools/db_sync.py upload --allow-duplicates

OLD USAGE (deprecated):
  python database/tools/local_db_upload.py
  python database/tools/local_db_upload.py --allow-duplicates

Environment:
  - SQLITE_PATH (optional) to override the SQLite DB path (defaults to database/inventory.db)
  - DATABASE_URL (required) the Render Postgres URL
"""

import argparse
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, List
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import env_loader  # loads .env into os.environ
from sqlalchemy import create_engine, text


# ---------- CONFIG ----------

SQLITE_PATH = Path(os.environ.get("SQLITE_PATH") or REPO_ROOT / "database" / "inventory.db")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Please set DATABASE_URL to your Render PostgreSQL URL before running this script.")

# Render sometimes gives postgres://, SQLAlchemy prefers postgresql+psycopg2://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)

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
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def connect_postgres(url: str):
    engine = create_engine(url, future=True, connect_args={"options": "-c client_encoding=UTF8"})
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
                print(f"    truncate failed for {table} ({exc}); falling back to DELETE/IDENTITY reset.")
                conn.execute(text(f"DELETE FROM {table};"))
                conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 1, false);"))
    print("Truncate done.\n")


def normalize_title(title: str) -> str:
    """Lightweight normalization to catch obvious dupes (spacing/punctuation/variant characters)."""
    normalized = (title or "").strip().lower()
    replacements = {
        "　": "",  # full-width space
        " ": "",
        "．": ".",
        "・": "",
        "･": "",
        "祕": "秘",
    }
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)
    normalized = re.sub(r"[\\s\\t\\r\\n\\-_.。．,，、:：;；!！?？'\"“”‘’()（）【】《》「」『』·•／/]+", "", normalized)
    return normalized


def find_duplicate_titles(sqlite_conn) -> Dict[str, List[sqlite3.Row]]:
    rows = sqlite_conn.execute("SELECT id, title, author, topics, cover_link FROM book_title").fetchall()
    buckets: Dict[str, List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        key = normalize_title(row["title"])
        if key:
            buckets[key].append(row)
    return {k: v for k, v in buckets.items() if len(v) > 1}


def render_duplicates(dupes: Dict[str, List[sqlite3.Row]]):
    print("[validation] Potential duplicate titles detected:")
    for key, items in dupes.items():
        print(f"  key='{key}' count={len(items)}")
        for row in items:
            flags = []
            if not row["topics"]:
                flags.append("missing topics")
            if not row["cover_link"]:
                flags.append("missing cover_link")
            flag_text = f" [{' | '.join(flags)}]" if flags else ""
            print(
                f"    id={row['id']:>4} | title={row['title']} | author={row['author'] or '-'}{flag_text}"
            )
    print("To proceed anyway, rerun with --allow-duplicates.\n")


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
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    insert_sql = text(f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})")

    print(f"  Found {len(rows)} rows in SQLite.")
    inserted = 0
    with pg_engine.begin() as conn:
        for row in rows:
            payload = {}
            for k, v in dict(row).items():
                if isinstance(v, bytes):
                    payload[k] = v.decode("utf-8", errors="replace")
                else:
                    payload[k] = v
            conn.execute(insert_sql, payload)
            inserted += 1

    print(f"  Inserted {inserted} rows into Postgres.{os.linesep}")


def fix_sequences(pg_engine):
    """Ensure Postgres sequences are set to MAX(id) for each table."""
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
            conn.execute(
                text(
                    f"""
                    SELECT setval(
                        '{seq}',
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        true
                    );
                    """
                )
            )

    print("Sequence fix complete.\n")


def main():
    parser = argparse.ArgumentParser(description="Upload local SQLite data to Postgres with validation.")
    parser.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="Proceed even if potential duplicate book titles are detected.",
    )
    args = parser.parse_args()

    print(f"Using SQLite: {SQLITE_PATH}")
    print(f"Using Postgres: {DATABASE_URL}\n")

    sqlite_conn = connect_sqlite(SQLITE_PATH)

    duplicates = find_duplicate_titles(sqlite_conn)
    if duplicates and not args.allow_duplicates:
        render_duplicates(duplicates)
        print("[abort] Resolve duplicates in SQLite before uploading to Postgres.")
        return 1

    pg_engine = connect_postgres(DATABASE_URL)

    truncate_tables(pg_engine, TABLES)

    for table in TABLES:
        migrate_table(sqlite_conn, pg_engine, table)

    fix_sequences(pg_engine)

    print("✅ Migration complete! You can now check your Postgres DB.")


if __name__ == "__main__":
    main()
