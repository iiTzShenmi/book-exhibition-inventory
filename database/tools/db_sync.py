#!/usr/bin/env python
"""
Unified tool for syncing local SQLite database to PostgreSQL cloud database.

This tool combines validation, cleaning, and upload functionality into one simple script.
It replaces the old local_db_upload.py and local_db_sync.py files.

⚠️ IMPORTANT: This is the MAIN file to use. Do not call local_db_upload.py or 
local_db_sync.py directly - they are deprecated and will be removed in the future.

Usage:
  python database/tools/db_sync.py upload [--allow-duplicates] [--verbose]
  python database/tools/db_sync.py push [--auto-fix] [--fill-metadata] [--verbose]
  python database/tools/db_sync.py diagnose [--local-only] [--cloud-only] [--verbose]
  python database/tools/db_sync.py clean [--purge-null] [--dedupe] [--auto-fix] [--verbose]
"""

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from urllib.parse import urlparse, urlunparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools import env_loader  # noqa: F401
from sqlalchemy import create_engine, text

# ---------- CONFIG ----------

SQLITE_PATH = Path(os.environ.get("SQLITE_PATH") or ROOT_DIR / "database" / "inventory.db")
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Please set DATABASE_URL to your Render PostgreSQL URL.")

# Fix postgres:// to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)

TABLES = ["admin_user", "admin_invite", "cabinet", "book_title", "inventory", "view_event", "audit_log"]


# ---------- HELPER FUNCTIONS ----------

def build_env(local: bool = False) -> Dict[str, str]:
    """Build environment for subprocess calls."""
    env = os.environ.copy()
    if local:
        env["DATABASE_URL"] = ""
        env["SKIP_INIT"] = "1"
    else:
        env["SKIP_INIT"] = ""
    return env


def run_cmd(label: str, cmd: List[str], env: Optional[Dict[str, str]] = None, verbose: bool = False) -> int:
    """Run a command and return exit code."""
    print(f"[run] {label}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT_DIR, env=env, capture_output=not verbose, text=True)
    if not verbose:
        # Always show output for important commands
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
    status = "OK" if proc.returncode == 0 else f"FAIL ({proc.returncode})"
    print(f"[done] {label}: {status}")
    return proc.returncode


def mask_uri(uri: str) -> str:
    """Hide credentials before writing database URLs to logs."""
    try:
        parsed = urlparse(uri)
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        cred = user
        if user and parsed.password:
            cred += ":****"
        netloc = f"{cred}@{host}{port}" if cred else f"{host}{port}"
        return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return "<masked-db-uri>"


def prompt_yes(question: str, default: bool = True) -> bool:
    """Prompt user for yes/no."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    reply = input(question + suffix).strip().lower()
    return default if not reply else reply.startswith("y")


# ---------- DATABASE OPERATIONS ----------

def connect_sqlite(path: Path) -> sqlite3.Connection:
    """Connect to SQLite database."""
    if not path.exists():
        raise FileNotFoundError(f"SQLite DB not found at {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def connect_postgres(url: str):
    """Connect to PostgreSQL database."""
    return create_engine(url, future=True, connect_args={"options": "-c client_encoding=UTF8"})


def normalize_title(title: str) -> str:
    """Normalize title for duplicate detection."""
    normalized = (title or "").strip().lower()
    replacements = {"　": "", " ": "", "．": ".", "・": "", "･": "", "祕": "秘"}
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)
    normalized = re.sub(r"[\s\t\r\n\-_.。．,，、:：;；!！?？'\"""''()（）【】《》「」『』·•／/]+", "", normalized)
    return normalized


def find_duplicates(sqlite_conn) -> Dict[str, List[sqlite3.Row]]:
    """Find duplicate titles in SQLite."""
    rows = sqlite_conn.execute("SELECT id, title, author, topics, cover_link FROM book_title").fetchall()
    buckets: Dict[str, List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        key = normalize_title(row["title"])
        if key:
            buckets[key].append(row)
    return {k: v for k, v in buckets.items() if len(v) > 1}


def print_duplicates(dupes: Dict[str, List[sqlite3.Row]]):
    """Print duplicate titles."""
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
            print(f"    id={row['id']:>4} | title={row['title']} | author={row['author'] or '-'}{flag_text}")
    print()


def truncate_postgres(engine, tables: List[str]):
    """Clear PostgreSQL tables."""
    print("Truncating Postgres tables...")
    with engine.begin() as conn:
        for table in tables:
            try:
                conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))
            except Exception:
                conn.execute(text(f"DELETE FROM {table};"))
                conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 1, false);"))
    print("Done.\n")


def migrate_table(sqlite_conn: sqlite3.Connection, pg_engine, table_name: str):
    """Migrate a single table from SQLite to PostgreSQL."""
    print(f"Migrating {table_name}...")
    try:
        rows = sqlite_conn.execute(f"SELECT * FROM {table_name}").fetchall()
    except sqlite3.OperationalError:
        print(f"  Skip: table '{table_name}' not found.\n")
        return
    if not rows:
        print(f"  No rows.\n")
        return

    # Get columns, but exclude quantity columns if they exist
    cols = list(rows[0].keys())
    # Remove quantity columns that shouldn't be migrated
    cols_to_exclude = {"qty_on_hand", "qty_reserved"}
    cols = [c for c in cols if c not in cols_to_exclude]
    
    if not cols:
        print(f"  No valid columns to migrate.\n")
        return
    
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    insert_sql = text(f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})")

    with pg_engine.begin() as conn:
        for row in rows:
            row_dict = dict(row)
            # Only include columns we want to migrate
            payload = {k: (v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v) 
                      for k, v in row_dict.items() if k in cols}
            conn.execute(insert_sql, payload)

    print(f"  Inserted {len(rows)} rows.\n")


def fix_sequences(pg_engine):
    """Fix PostgreSQL ID sequences."""
    print("Fixing ID sequences...")
    sequences = {
        "admin_user": "admin_user_id_seq",
        "admin_invite": "admin_invite_id_seq",
        "cabinet": "cabinet_id_seq",
        "book_title": "book_title_id_seq",
        "inventory": "inventory_id_seq",
        "view_event": "view_event_id_seq",
        "audit_log": "audit_log_id_seq",
    }
    with pg_engine.begin() as conn:
        for table, seq in sequences.items():
            conn.execute(text(f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {table}), 1), true);"))
    print("Done.\n")


# ---------- COMMAND FUNCTIONS ----------

def run_diagnose(local: bool, verbose: bool = False) -> int:
    """Run database diagnose."""
    env = build_env(local=local)
    label = "local" if local else "cloud"
    cmd = [sys.executable, "-m", "database.tools.db_tools", "diagnose"]
    return run_cmd(f"diagnose-{label}", cmd, env=env, verbose=verbose)


def run_purge_null(local: bool, apply: bool = False, verbose: bool = False) -> int:
    """Run purge-null."""
    env = build_env(local=local)
    label = "local" if local else "cloud"
    cmd = [sys.executable, "-m", "database.tools.db_tools", "purge-null"]
    if not apply:
        cmd.append("--dry-run")
    return run_cmd(f"purge-null-{label}", cmd, env=env, verbose=verbose)


def run_dedupe(local: bool, prompt: bool = False, verbose: bool = False) -> int:
    """Run dedupe."""
    env = build_env(local=local)
    label = "local" if local else "cloud"
    cmd = [sys.executable, "-m", "database.tools.db_tools", "dedupe", "--fix-null-inventory"]
    if prompt:
        cmd.append("--prompt")
    return run_cmd(f"dedupe-{label}", cmd, env=env, verbose=verbose)


def run_sync_csv(verbose: bool = False) -> int:
    """Sync CSV to local database."""
    env = build_env(local=True)
    env["ENABLE_CSV_SYNC"] = "1"
    cmd = [sys.executable, "-m", "database.tools.db_tools", "sync-csv"]
    return run_cmd("sync-csv", cmd, env=env, verbose=verbose)


def run_metadata_fill(limit: int = 50, verbose: bool = False):
    """Fill missing metadata."""
    env = build_env(local=True)
    fetchers = [
        ("fetch-covers", [sys.executable, "tools/fetch_cover_url.py", "--limit", str(limit), "--drop-missing", "--force-drop-missing"]),
        ("fetch-authors", [sys.executable, "tools/fetch_author.py", "--limit", str(limit)]),
        ("fetch-topics", [sys.executable, "tools/fetch_topics.py", "--limit", str(limit)]),
    ]
    for label, cmd in fetchers:
        run_cmd(label, cmd, env=env, verbose=verbose)


def ensure_clean(auto_fix: bool = False, verbose: bool = False) -> bool:
    """Ensure local database is clean."""
    attempts = 0
    while attempts < 3:
        attempts += 1
        if run_diagnose(local=True, verbose=verbose) == 0:
            return True

        if auto_fix or prompt_yes("Local diagnose failed. Run purge-null?", default=True):
            run_purge_null(local=True, apply=True, verbose=verbose)
        if auto_fix or prompt_yes("Run dedupe?", default=True):
            run_dedupe(local=True, prompt=not auto_fix, verbose=verbose)

    print("[abort] diagnose still failing after 3 attempts.")
    return False


def cmd_upload(args) -> int:
    """Upload SQLite to PostgreSQL."""
    print(f"Using SQLite: {SQLITE_PATH}")
    print(f"Using Postgres: {mask_uri(DATABASE_URL)}\n")

    # Check for duplicates
    sqlite_conn = connect_sqlite(SQLITE_PATH)
    duplicates = find_duplicates(sqlite_conn)
    if duplicates and not args.allow_duplicates:
        print_duplicates(duplicates)
        print("[abort] Resolve duplicates first or use --allow-duplicates")
        return 1

    # Upload
    pg_engine = connect_postgres(DATABASE_URL)
    
    # Drop quantity columns from PostgreSQL before migrating
    print("Checking PostgreSQL schema...")
    with pg_engine.connect() as check_conn:
        check = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'inventory' 
            AND column_name IN ('qty_on_hand', 'qty_reserved')
        """)
        existing = [row[0] for row in check_conn.execute(check).fetchall()]
        if existing:
            print(f"[migration] Dropping quantity columns from PostgreSQL: {existing}")
            with pg_engine.begin() as trans_conn:
                for col in existing:
                    trans_conn.execute(text(f"ALTER TABLE inventory DROP COLUMN IF EXISTS {col}"))
            print("[migration] Quantity columns removed from PostgreSQL.\n")
    
    truncate_postgres(pg_engine, TABLES)
    for table in TABLES:
        migrate_table(sqlite_conn, pg_engine, table)
    fix_sequences(pg_engine)

    print("✅ Upload complete!")
    return 0


def cmd_push(args) -> int:
    """Full workflow: validate, clean, optionally fill metadata, then upload."""
    # Sync CSV if needed
    if not args.no_sync_csv and Path("database/inventory.csv").exists():
        if run_sync_csv(verbose=args.verbose) != 0:
            return 1

    # Clean local database
    if not ensure_clean(auto_fix=args.auto_fix, verbose=args.verbose):
        return 1

    # Fill metadata if requested
    if args.fill_metadata:
        run_metadata_fill(limit=args.fetch_limit, verbose=args.verbose)
        if not ensure_clean(auto_fix=args.auto_fix, verbose=args.verbose):
            return 1

    # Upload
    if cmd_upload(args) != 0:
        return 1

    # Final check
    if not args.skip_cloud_diagnose:
        return run_diagnose(local=False, verbose=args.verbose)
    return 0


def cmd_diagnose(args) -> int:
    """Run diagnose on local and/or cloud."""
    failures = 0
    if not args.local_only:
        failures += run_diagnose(local=False, verbose=args.verbose)
    if not args.cloud_only:
        failures += run_diagnose(local=True, verbose=args.verbose)
    return 0 if failures == 0 else 1


def cmd_clean(args) -> int:
    """Clean local database (purge-null + dedupe)."""
    if args.purge_null:
        run_purge_null(local=True, apply=True, verbose=args.verbose)
    if args.dedupe:
        run_dedupe(local=True, prompt=not args.auto_fix, verbose=args.verbose)
    return 0


# ---------- MAIN ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync local SQLite to PostgreSQL")
    sub = parser.add_subparsers(dest="command", required=True)

    # Upload command
    upload = sub.add_parser("upload", help="Upload SQLite to PostgreSQL")
    upload.add_argument("--allow-duplicates", action="store_true", help="Allow duplicates")
    upload.add_argument("--verbose", action="store_true", help="Verbose output")
    upload.set_defaults(func=cmd_upload)

    # Push command (full workflow)
    push = sub.add_parser("push", help="Full workflow: validate, clean, upload")
    push.add_argument("--no-sync-csv", action="store_true", help="Skip CSV sync")
    push.add_argument("--fill-metadata", action="store_true", help="Fill metadata before upload")
    push.add_argument("--fetch-limit", type=int, default=50, help="Metadata fetch limit")
    push.add_argument("--skip-cloud-diagnose", action="store_true", help="Skip final diagnose")
    push.add_argument("--auto-fix", action="store_true", help="Auto-fix issues")
    push.add_argument("--allow-duplicates", action="store_true", help="Allow duplicates")
    push.add_argument("--verbose", action="store_true", help="Verbose output")
    push.set_defaults(func=cmd_push)

    # Diagnose command
    diag = sub.add_parser("diagnose", help="Run diagnose")
    diag.add_argument("--cloud-only", action="store_true", help="Only cloud")
    diag.add_argument("--local-only", action="store_true", help="Only local")
    diag.add_argument("--verbose", action="store_true", help="Verbose output")
    diag.set_defaults(func=cmd_diagnose)

    # Clean command
    clean = sub.add_parser("clean", help="Clean local database")
    clean.add_argument("--purge-null", action="store_true", help="Run purge-null")
    clean.add_argument("--dedupe", action="store_true", help="Run dedupe")
    clean.add_argument("--auto-fix", action="store_true", help="Auto-fix (no prompts)")
    clean.add_argument("--verbose", action="store_true", help="Verbose output")
    clean.set_defaults(func=cmd_clean)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

