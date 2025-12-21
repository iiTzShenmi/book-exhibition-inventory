"""
Unified maintenance utilities for the inventory database.

Usage examples (run from repo root):
  python -m database.tools.db_tools sync-csv
  python -m database.tools.db_tools drop-missing --file database/tools/book_csv_missing.txt
  python -m database.tools.db_tools drop-missing --force
"""

import argparse
import os
import re
import sys
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# StaleDataError import - try both locations for compatibility
try:
    from sqlalchemy.orm.exc import StaleDataError
except ImportError:
    # SQLAlchemy 2.0+ might have it in a different location
    try:
        from sqlalchemy.exc import StaleDataError
    except ImportError:
        # If it doesn't exist, we'll catch it as a general exception
        StaleDataError = Exception

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app, sync_csv_to_db, CSV_PATH  # noqa: E402
from database.models import db, BookTitle, Inventory  # noqa: E402


REPORT_DIR = Path(SCRIPT_DIR) / "reports"
TOOLS_DIR = Path(ROOT_DIR) / "tools"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_titles(path: str) -> list[str]:
    titles: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            title = line.split("\t")[0].strip()
            if title:
                titles.append(title)
    return titles


def drop_titles(titles: Iterable[str], *, force: bool = False) -> Tuple[int, int, int]:
    """Drop titles and related inventory rows."""
    removed = 0
    skipped = 0
    missing = 0

    for title in titles:
        title_obj = BookTitle.query.filter_by(title=title).first()
        if not title_obj:
            missing += 1
            print(f"[skip] not found in DB: {title}")
            continue

        inventories = Inventory.query.filter_by(title_id=title_obj.id).all()
        # Quantity tracking removed - check if any inventory exists
        if len(inventories) > 0 and not force:
            skipped += 1
            print(f"[skip] has inventory ({len(inventories)} records) :: {title}")
            continue

        for inv in inventories:
            db.session.delete(inv)
        db.session.delete(title_obj)
        removed += 1
        print(f"[delete] {title}")

    db.session.commit()
    return removed, skipped, missing


def cmd_sync_csv(_: argparse.Namespace) -> int:
    print(f"[sync-csv] starting import from {CSV_PATH}")
    with app.app_context():
        sync_csv_to_db()
    print("[sync-csv] done")
    return 0


def cmd_drop_missing(args: argparse.Namespace) -> int:
    # Use logs directory for missing files
    logs_dir = os.path.join(ROOT_DIR, "database", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    list_path = args.file or os.path.join(logs_dir, "book_csv_missing.txt")
    if not os.path.exists(list_path):
        print(f"[error] file not found: {list_path}")
        return 1

    titles = load_titles(list_path)
    if not titles:
        print("[info] no titles to process.")
        return 0

    with app.app_context():
        removed, skipped, missing = drop_titles(titles, force=args.force)

    try:
        with open(list_path, "w", encoding="utf-8") as f:
            f.write("")
        print(f"[info] cleared list file: {list_path}")
    except Exception as exc:  # pragma: no cover - best-effort
        print(f"[warn] failed to clear list file {list_path}: {exc}")

    print(f"[done] removed={removed}, skipped={skipped}, not_found={missing}")
    return 0


def normalize_title(title: str) -> str:
    norm = (title or "").strip().lower()
    replacements = {
        "　": "",
        " ": "",
        "．": ".",
        "祕": "秘",
        "・": "",
        "･": "",
    }
    for src, dst in replacements.items():
        norm = norm.replace(src, dst)
    norm = re.sub(r"[\\s\\t\\r\\n\\-_.。．,，、:：;；!！?？'\"“”‘’()（）【】《》「」『』·•／/]+", "", norm)
    return norm


def is_blank(value: str | None) -> bool:
    """Return True for None, empty/whitespace, 'null', 'none', 'n/a', or JSON-ish empties."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"null", "none", "n/a"}:
        return True
    if text in {"[]", "{}"}:
        return True
    return False


def topics_missing(raw: str | None) -> bool:
    """Detect whether topics field is effectively empty."""
    if is_blank(raw):
        return True
    try:
        data = json.loads(raw)
        if isinstance(data, (list, tuple, set)):
            return len([str(item).strip() for item in data if str(item).strip()]) == 0
    except Exception:
        # If malformed JSON, treat as missing so it shows up in reports
        return True
    return False


def check_db(_: argparse.Namespace) -> int:
    """Check for common data quality issues."""
    issues_found = False
    dup_path = REPORT_DIR / "duplicate_titles.tsv"
    missing_cover_path = TOOLS_DIR / "missing_covers.txt"
    missing_topics_path = TOOLS_DIR / "missing_topics.txt"
    missing_author_path = TOOLS_DIR / "missing_authors.txt"
    orphan_path = REPORT_DIR / "orphan_inventory.tsv"
    malformed_topics_path = REPORT_DIR / "malformed_topics.tsv"
    null_title_path = REPORT_DIR / "null_titles.tsv"

    def write_lines(path: Path, lines: List[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")

    with app.app_context():
        # Basic sanity: null/blank titles (should not happen)
        null_titles = [bt for bt in BookTitle.query.all() if is_blank(bt.title)]
        if null_titles:
            issues_found = True
            print(f"[issue] title rows with null/blank title field: {len(null_titles)}")
            for bt in null_titles:
                print(f"    id={bt.id} title={bt.title!r}")
            write_lines(null_title_path, [f"{bt.id}\t{bt.title!r}" for bt in null_titles])
            print(f"  -> wrote null/blank title list: {null_title_path}")

        # Duplicates by normalized title
        buckets: Dict[str, List[BookTitle]] = {}
        for bt in BookTitle.query.all():
            key = normalize_title(bt.title or "")
            if not key:
                continue
            buckets.setdefault(key, []).append(bt)
        dupes = {k: v for k, v in buckets.items() if len(v) > 1}
        if dupes:
            issues_found = True
            print("[issue] potential duplicate titles (normalized):")
            dup_lines = ["key\ttitle_id\ttitle\tflags"]
            for key, items in dupes.items():
                print(f"  key='{key}' count={len(items)}")
                for bt in items:
                    flags = []
                    if not bt.cover_link:
                        flags.append("no_cover")
                    if not bt.author:
                        flags.append("no_author")
                    if not bt.topics:
                        flags.append("no_topics")
                    flag_txt = f" [{' | '.join(flags)}]" if flags else ""
                    print(f"    id={bt.id} title={bt.title}{flag_txt}")
                    dup_lines.append(f"{key}\t{bt.id}\t{bt.title}\t{','.join(flags)}")
            write_lines(dup_path, dup_lines)
            print(f"  -> wrote duplicate report: {dup_path}")

        # Missing metadata
        missing_cover = [bt for bt in BookTitle.query.all() if is_blank(bt.cover_link)]
        if missing_cover:
            issues_found = True
            print(f"[issue] titles missing cover_link: {len(missing_cover)}")
            for bt in missing_cover[:20]:
                print(f"    id={bt.id} title={bt.title}")
            if len(missing_cover) > 20:
                print(f"    ...and {len(missing_cover) - 20} more")
            write_lines(missing_cover_path, [bt.title for bt in missing_cover])
            print(f"  -> wrote cover list for fetcher: {missing_cover_path}")

        missing_topics = [bt for bt in BookTitle.query.all() if topics_missing(bt.topics)]
        if missing_topics:
            issues_found = True
            print(f"[issue] titles missing topics: {len(missing_topics)}")
            for bt in missing_topics[:20]:
                print(f"    id={bt.id} title={bt.title}")
            if len(missing_topics) > 20:
                print(f"    ...and {len(missing_topics) - 20} more")
            write_lines(missing_topics_path, [bt.title for bt in missing_topics])
            print(f"  -> wrote topics list for fetcher: {missing_topics_path}")

        missing_author = [bt for bt in BookTitle.query.all() if is_blank(bt.author)]
        if missing_author:
            issues_found = True
            print(f"[issue] titles missing author: {len(missing_author)}")
            for bt in missing_author[:20]:
                print(f"    id={bt.id} title={bt.title}")
            if len(missing_author) > 20:
                print(f"    ...and {len(missing_author) - 20} more")
            write_lines(missing_author_path, [bt.title for bt in missing_author])
            print(f"  -> wrote author list for fetcher: {missing_author_path}")

        # Malformed topics JSON (non-empty string but fails json.loads)
        malformed_topics = []
        for bt in BookTitle.query.all():
            raw = bt.topics
            if is_blank(raw):
                continue
            if isinstance(raw, str):
                try:
                    data = json.loads(raw)
                    if not isinstance(data, (list, tuple, set)):
                        malformed_topics.append(bt)
                except Exception:
                    malformed_topics.append(bt)
        if malformed_topics:
            issues_found = True
            print(f"[issue] titles with malformed topics JSON: {len(malformed_topics)}")
            for bt in malformed_topics[:10]:
                print(f"    id={bt.id} title={bt.title} topics={bt.topics!r}")
            if len(malformed_topics) > 10:
                print(f"    ...and {len(malformed_topics) - 10} more")
            write_lines(
                malformed_topics_path,
                [f"{bt.id}\t{bt.title}\t{bt.topics!r}" for bt in malformed_topics],
            )
            print(f"  -> wrote malformed topics list: {malformed_topics_path}")

        # Quantity tracking removed - no quantity checks needed

        # Check for NULL title_id (critical issue) - use count() which works better than all()
        null_title_id_count = Inventory.query.filter(Inventory.title_id.is_(None)).count()
        if null_title_id_count > 0:
            issues_found = True
            print(f"[issue] inventory rows with NULL title_id (CRITICAL): {null_title_id_count}")
            
            # Try to get details via raw SQL (ORM .all() might fail with NULL foreign keys)
            null_title_id_details = db.session.execute(
                text("SELECT id, title_id, cabinet_id FROM inventory WHERE title_id IS NULL")
            ).fetchall()
            
            if null_title_id_details:
                for row in null_title_id_details[:10]:
                    print(f"    inventory_id={row[0]} title_id={row[1]} cabinet_id={row[2]}")
                if len(null_title_id_details) > 10:
                    print(f"    ...and {len(null_title_id_details) - 10} more")
                write_lines(
                    orphan_path,
                    [
                        f"{row[0]}\t{row[1]}\t{row[2]}\tNULL_TITLE_ID"
                        for row in null_title_id_details
                    ],
                )
            else:
                # SQL query didn't return rows but count says they exist
                print(f"    (Unable to fetch details - {null_title_id_count} rows exist with NULL title_id)")
                write_lines(
                    orphan_path,
                    [f"UNKNOWN\tNULL\tUNKNOWN\tNULL_TITLE_ID (count={null_title_id_count})"],
                )
            
            print(f"  -> wrote NULL title_id inventory report: {orphan_path}")
            print(f"  -> Fix with: python -m database.tools.db_tools purge-null")

        orphaned_inventory = (
            db.session.query(Inventory)
            .outerjoin(BookTitle, Inventory.title_id == BookTitle.id)
            .filter(BookTitle.id.is_(None))
            .filter(Inventory.title_id.isnot(None))  # exclude NULL title_id (already reported above)
            .all()
        )
        if orphaned_inventory:
            issues_found = True
            print(f"[issue] inventory rows without matching book_title: {len(orphaned_inventory)}")
            for inv in orphaned_inventory[:10]:
                print(f"    inventory_id={inv.id} title_id={inv.title_id} cabinet_id={inv.cabinet_id}")
            if len(orphaned_inventory) > 10:
                print(f"    ...and {len(orphaned_inventory) - 10} more")
            write_lines(
                orphan_path,
                [
                    f"{inv.id}\t{inv.title_id}\t{inv.cabinet_id}\tORPHANED"
                    for inv in orphaned_inventory
                ],
            )
            print(f"  -> wrote orphan inventory report: {orphan_path}")

    if not issues_found:
        print("[check] no issues detected.")
        return 0
    print("[check] review issues above. Follow-up:")
    print(f"  - run cover fetcher: python tools/fetch_cover_url.py (uses {missing_cover_path})")
    print(f"  - run author fetcher: python tools/fetch_author.py (uses {missing_author_path})")
    print(f"  - run topics fetcher: python tools/fetch_topics.py (uses {missing_topics_path})")
    print(f"  - inspect duplicates: {dup_path}")
    print(f"  - inspect orphan inventory: {orphan_path}")
    print(f"  - inspect malformed topics: {malformed_topics_path}")
    print(f"  - inspect null/blank titles: {null_title_path}")
        # Check if we found NULL title_id issues
    with app.app_context():
        null_count = Inventory.query.filter(Inventory.title_id.is_(None)).count()
        if null_count > 0:
            print(f"  - CRITICAL: Fix NULL title_id with: python -m database.tools.db_tools purge-null")
    return 1


def cmd_purge_null(args: argparse.Namespace) -> int:
    """
    Hard-delete rows that contain NULL in any column (BookTitle/Inventory).
    Deletes inventory rows first to avoid FK issues.
    """
    with app.app_context():
        # Use both ORM and raw SQL to find NULL values (ORM count works, but all() might fail)
        # Check for NULL title_id in inventory (CRITICAL - violates NOT NULL constraint)
        null_title_id_count = Inventory.query.filter(Inventory.title_id.is_(None)).count()
        
        # Also try raw SQL as backup
        null_title_id_inv_sql = db.session.execute(
            text("SELECT id, title_id, cabinet_id FROM inventory WHERE title_id IS NULL")
        ).fetchall()
        
        # Use count from ORM (more reliable), but get details from SQL if needed
        if null_title_id_count > 0:
            print(f"[purge-null] Found {null_title_id_count} inventory rows with NULL title_id (using ORM count)")
            if not null_title_id_inv_sql:
                print(f"[purge-null] Warning: ORM found {null_title_id_count} rows but SQL query returned empty. Using direct DELETE.")
        
        # Check for NULL cabinet_id in inventory
        null_cabinet_id_inv = db.session.execute(
                text("SELECT id, title_id, cabinet_id FROM inventory WHERE cabinet_id IS NULL")
        ).fetchall()
        
        # Combine all NULL inventory issues (avoid duplicates)
        inv_null_ids = set()
        inv_nulls_details = []
        
        # Handle NULL title_id - use SQL results if available, otherwise we know count > 0
        if null_title_id_count > 0:
            if null_title_id_inv_sql:
                for row in null_title_id_inv_sql:
                    inv_null_ids.add(row[0])
                    inv_nulls_details.append((row[0], ["title_id"], row[1], row[2]))
            else:
                # SQL query failed but ORM count says rows exist - add placeholder
                print(f"[purge-null] SQL query didn't return rows, but {null_title_id_count} rows exist. Will delete directly.")
                # We'll delete by SQL directly below
        for row in null_cabinet_id_inv:
            if row[0] not in inv_null_ids:
                inv_null_ids.add(row[0])
                inv_nulls_details.append((row[0], ["cabinet_id"], row[1], row[2]))
        # Quantity tracking removed - no NULL quantity checks

        # Check BookTitle for NULL values
        bt_nulls: List[Tuple[int, List[str]]] = []
        for bt in BookTitle.query.all():
            fields = {
                "title": bt.title,
                "author": bt.author,
                "topics": bt.topics,
                "cover_link": bt.cover_link,
            }
            null_fields = [name for name, val in fields.items() if val is None]
            if null_fields:
                bt_nulls.append((bt.id, null_fields))

        # Any inventory pointing to a soon-to-be-deleted book_title must also be removed
        bt_ids = [bt_id for bt_id, _ in bt_nulls]
        inv_fk_bt = []
        if bt_ids:
            inv_fk_bt = (
                Inventory.query.filter(Inventory.title_id.in_(bt_ids)).all()
            )

        # Calculate total - always include NULL title_id count even if SQL SELECT didn't return rows
        total_inv_nulls = max(len(inv_nulls_details), null_title_id_count) if null_title_id_count > 0 else len(inv_nulls_details)
        total = len(bt_nulls) + total_inv_nulls + len(inv_fk_bt)
        
        if total == 0:
            print("[purge-null] no NULL-containing rows found.")
            return 0
        
        # If we detected NULL title_id via count but SQL didn't return details, show that
        if null_title_id_count > 0:
            if not null_title_id_inv_sql:
                print(f"[purge-null] Found {null_title_id_count} inventory rows with NULL title_id (detected via ORM count, SQL SELECT returned empty)")
            else:
                print(f"[purge-null] Found {null_title_id_count} inventory rows with NULL title_id")

        print(f"[purge-null] found {total} rows to delete "
              f"({total_inv_nulls} inventory with NULLs, {len(inv_fk_bt)} inventory referencing NULL titles, {len(bt_nulls)} book_title)")
        if inv_nulls_details:
            for inv_id, nf, title_id, cabinet_id in inv_nulls_details[:10]:
                print(f"  inventory id={inv_id} null_fields={nf} title_id={title_id} cabinet_id={cabinet_id}")
        elif null_title_id_count > 0:
            print(f"  (Found {null_title_id_count} rows with NULL title_id - details unavailable)")
        extra_inv = inv_fk_bt[: max(0, 10 - len(inv_nulls_details))]
        if extra_inv:
            for inv in extra_inv:
                print(f"  inventory id={inv.id} (refers to book_title id={inv.title_id})")
        if len(inv_nulls_details) + len(inv_fk_bt) > 10:
            print(f"  ...and {len(inv_nulls_details) + len(inv_fk_bt) - 10} more inventory rows")

        for bt_id, nf in bt_nulls[:10]:
            bt = BookTitle.query.get(bt_id)
            title_str = bt.title if bt else "?"
            print(f"  book_title id={bt_id} null_fields={nf} title={title_str!r}")
        if len(bt_nulls) > 10:
            print(f"  ...and {len(bt_nulls) - 10} more book_title rows")

        if args.dry_run:
            print("[purge-null] dry-run; no rows deleted.")
            return 0

        # Delete inventory first: those with NULLs and those referencing the bad titles
        # Use raw SQL to delete - ORM might not work with NULL foreign keys
        deleted_count = 0
        
        # Delete NULL title_id rows directly (most critical)
        # Always try to delete if count > 0, even if SQL SELECT didn't return rows
        if null_title_id_count > 0:
            print(f"[purge-null] Deleting {null_title_id_count} inventory rows with NULL title_id...")
            result = db.session.execute(text("DELETE FROM inventory WHERE title_id IS NULL"))
            deleted_count += result.rowcount
            print(f"[purge-null] Deleted {result.rowcount} rows with NULL title_id (expected {null_title_id_count})")
        
        # Delete NULL cabinet_id rows
        if null_cabinet_id_inv:
            result = db.session.execute(text("DELETE FROM inventory WHERE cabinet_id IS NULL"))
            deleted_count += result.rowcount
        
        # Quantity tracking removed - no NULL quantity checks/deletes needed
        
        # Delete inventory referencing bad book_titles
        if inv_fk_bt:
            fk_ids = [inv.id for inv in inv_fk_bt]
            db.session.query(Inventory).filter(Inventory.id.in_(fk_ids)).delete(
                synchronize_session=False
            )
            deleted_count += len(fk_ids)
        
        # Delete bad book_titles
        bt_deleted = 0
        if bt_ids:
            bt_deleted = db.session.query(BookTitle).filter(BookTitle.id.in_(bt_ids)).delete(
                synchronize_session=False
            )
        
        db.session.commit()
        print(f"[purge-null] deleted {deleted_count} inventory rows and {bt_deleted} book_title rows.")
    return 0


def pick_keeper(items: List[BookTitle]) -> BookTitle:
    """Pick a keeper record with most metadata, then newest updated_at, then smallest id."""
    def score(bt: BookTitle):
        filled = int(not is_blank(bt.cover_link)) + int(not is_blank(bt.author)) + int(not topics_missing(bt.topics))
        return (filled, bt.updated_at or bt.created_at or None, -bt.id)

    return sorted(items, key=lambda b: score(b), reverse=True)[0]


def describe_bt(bt: BookTitle) -> str:
    parts = [f"id={bt.id}", f"title='{bt.title}'"]
    meta = []
    meta.append("cover" if not is_blank(bt.cover_link) else "no_cover")
    meta.append("author" if not is_blank(bt.author) else "no_author")
    meta.append("topics" if not topics_missing(bt.topics) else "no_topics")
    parts.append(f"meta=({', '.join(meta)})")
    if bt.updated_at:
        parts.append(f"updated={bt.updated_at}")
    return " ".join(parts)


def prompt_keeper(key: str, items: List[BookTitle]) -> BookTitle | None:
    print(f"[dedupe] choose keeper for key='{key}' (enter id, or 's' to skip group):")
    for bt in items:
        print(f"  - {describe_bt(bt)}")
    choice = input("  keep id (blank=auto best, s=skip): ").strip()
    if not choice:
        return pick_keeper(items)
    if choice.lower() == "s":
        print("  skipping this group.")
        return None
    try:
        chosen_id = int(choice)
    except ValueError:
        print("  invalid input, auto-picking best.")
        return pick_keeper(items)
    for bt in items:
        if bt.id == chosen_id:
            return bt
    print("  id not in group, auto-picking best.")
    return pick_keeper(items)


def merge_metadata(keeper: BookTitle, others: List[BookTitle]):
    """Fill missing fields on keeper from duplicates."""
    for bt in others:
        if is_blank(keeper.cover_link) and not is_blank(bt.cover_link):
            keeper.cover_link = bt.cover_link
        if is_blank(keeper.author) and not is_blank(bt.author):
            keeper.author = bt.author
        if topics_missing(keeper.topics) and not topics_missing(bt.topics):
            keeper.topics = bt.topics


def merge_inventories(keeper: BookTitle, dup: BookTitle):
    """Reassign or merge inventory rows from dup into keeper."""
    # Defensive check before processing
    if keeper.id is None:
        raise ValueError(f"Cannot merge inventory: keeper BookTitle has NULL id")
    if dup.id is None:
        raise ValueError(f"Cannot merge inventory: duplicate BookTitle has NULL id")
    
    # Get all inventory records for the duplicate title
    with db.session.no_autoflush:
        inv_rows = Inventory.query.filter_by(title_id=dup.id).all()
    
    # Process each inventory record
    for inv in inv_rows:
        # Defensive check: ensure inv has valid title_id and cabinet_id
        if inv.title_id is None:
            print(f"[merge_inventories][warning] Skipping inventory id={inv.id} with NULL title_id")
            db.session.delete(inv)
            continue
        if inv.cabinet_id is None:
            print(f"[merge_inventories][warning] Skipping inventory id={inv.id} with NULL cabinet_id")
            db.session.delete(inv)
            continue
        
        # Check if inventory already exists for keeper title in this cabinet
        with db.session.no_autoflush:
            existing = Inventory.query.filter_by(title_id=keeper.id, cabinet_id=inv.cabinet_id).first()
        
        if existing:
            # Inventory already exists for keeper in this cabinet - delete the duplicate
            db.session.delete(inv)
        else:
            # Update title_id using raw SQL to avoid StaleDataError
            # This is safer than modifying the ORM object directly
            try:
                # First verify the record still exists and has the expected title_id
                result = db.session.execute(
                    text("SELECT id FROM inventory WHERE id = :inv_id AND title_id = :dup_id"),
                    {"inv_id": inv.id, "dup_id": dup.id}
                ).fetchone()
                
                if result:
                    # Record exists and matches - safe to update
                    update_result = db.session.execute(
                        text("UPDATE inventory SET title_id = :keeper_id WHERE id = :inv_id AND title_id = :dup_id"),
                        {"keeper_id": keeper.id, "inv_id": inv.id, "dup_id": dup.id}
                    )
                    if update_result.rowcount == 0:
                        # Record was modified or deleted - skip it
                        print(f"[merge_inventories][warning] Inventory id={inv.id} was modified during merge, skipping")
                        continue
                    # Expire the object so it reloads on next access
                    db.session.expire(inv)
                else:
                    # Record doesn't exist or was already updated - skip it
                    print(f"[merge_inventories][warning] Inventory id={inv.id} no longer exists or was already updated, skipping")
                    continue
            except Exception as e:
                # If update fails, log and skip this record
                # Don't rollback here - we want to keep other changes
                print(f"[merge_inventories][warning] Update failed for inventory id={inv.id}: {e}")
                continue


def cmd_dedupe(args: argparse.Namespace) -> int:
    """Merge duplicate titles by normalized name, optionally prompting for keeper."""
    with app.app_context():
        # First, check for and optionally fix NULL title_id issues
        null_title_inv_count = Inventory.query.filter(Inventory.title_id.is_(None)).count()
        if null_title_inv_count > 0:
            if args.fix_null_inventory:
                print(f"[dedupe] Found {null_title_inv_count} inventory rows with NULL title_id. Deleting them...")
                db.session.execute(text("DELETE FROM inventory WHERE title_id IS NULL"))
                db.session.commit()
                print(f"[dedupe] Removed {null_title_inv_count} inventory rows with null title_id.")
            else:
                print(f"[dedupe][error] Found {null_title_inv_count} inventory rows with NULL title_id.")
                print("  Run with --fix-null-inventory to delete them automatically,")
                print("  or run `python -m database.tools.db_tools purge-null` first.")
                return 1
        
        try:
            buckets: Dict[str, List[BookTitle]] = {}
            for bt in BookTitle.query.all():
                key = normalize_title(bt.title)
                if not key:
                    continue
                buckets.setdefault(key, []).append(bt)
            dup_groups = {k: v for k, v in buckets.items() if len(v) > 1}
            if not dup_groups:
                print("[dedupe] no duplicates detected. (Run `check` for a full report.)")
                return 0

            total_removed = 0
            for key, items in dup_groups.items():
                keeper = prompt_keeper(key, items) if args.prompt else pick_keeper(items)
                if not keeper:
                    continue
                others = [bt for bt in items if bt.id != keeper.id]
                merge_metadata(keeper, others)
                for bt in others:
                    # Merge inventories first (moves inventory to keeper)
                    merge_inventories(keeper, bt)
                    
                    # Delete any remaining inventory for this duplicate BookTitle
                    # (shouldn't be any after merge_inventories, but be safe)
                    # Use raw SQL to avoid triggering validators during cascade
                    remaining_count = db.session.execute(
                        text("SELECT COUNT(*) FROM inventory WHERE title_id = :title_id"),
                        {"title_id": bt.id}
                    ).scalar()
                    if remaining_count > 0:
                        db.session.execute(
                            text("DELETE FROM inventory WHERE title_id = :title_id"),
                            {"title_id": bt.id}
                        )
                    
                    # Now safe to delete the BookTitle (no inventory references it)
                    db.session.delete(bt)
                    total_removed += 1
                if args.verbose:
                    print(f"[dedupe] kept id={keeper.id} title='{keeper.title}' (merged {len(others)} records)")

            db.session.commit()
            print(f"[dedupe] completed. Removed {total_removed} duplicate title rows.")
        except StaleDataError as exc:
            # StaleDataError is unlikely with raw SQL, but handle it if it occurs
            db.session.rollback()
            print(f"[dedupe][error] StaleDataError: {exc}")
            print("  This usually means inventory records were modified during the merge.")
            print("  Try running the command again, or run `python -m database.tools.db_tools check` first.")
            return 1
        except IntegrityError as exc:
            db.session.rollback()
            print(f"[dedupe][error] database constraint failed: {exc.orig}")
            print("  Recommend: rerun `python -m database.tools.db_tools check` to inspect orphan inventory.")
            return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Database maintenance utilities.")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_cmd = sub.add_parser("sync-csv", help="Import inventory.csv into the configured DB.")
    sync_cmd.set_defaults(func=cmd_sync_csv)

    check_cmd = sub.add_parser("check", help="Run data-quality checks (duplicates, missing metadata).")
    check_cmd.set_defaults(func=check_db)

    diag_cmd = sub.add_parser("diagnose", help="Alias for `check`; runs full diagnostics.")
    diag_cmd.set_defaults(func=check_db)

    dedupe_cmd = sub.add_parser(
        "dedupe", help="Auto-merge duplicate titles (keeps best metadata, merges inventories)."
    )
    dedupe_cmd.add_argument("--verbose", action="store_true", help="Print each merged group.")
    dedupe_cmd.add_argument(
        "--prompt",
        action="store_true",
        help="Interactively choose which record to keep for each duplicate group.",
    )
    dedupe_cmd.add_argument(
        "--fix-null-inventory",
        action="store_true",
        help="Delete inventory rows with null title_id before merging (avoids constraint errors).",
    )
    dedupe_cmd.set_defaults(func=cmd_dedupe)

    drop_cmd = sub.add_parser("drop-missing", help="Drop titles listed in a file.")
    logs_dir = os.path.join(ROOT_DIR, "database", "logs")
    drop_cmd.add_argument(
        "--file",
        default=os.path.join(logs_dir, "book_csv_missing.txt"),
        help="Path to title list (default: database/logs/book_csv_missing.txt).",
    )
    drop_cmd.add_argument(
        "--force",
        action="store_true",
        help="Force delete even if inventory quantities are > 0.",
    )
    drop_cmd.set_defaults(func=cmd_drop_missing)

    purge_cmd = sub.add_parser(
        "purge-null",
        help="Delete rows that contain NULL in any column (Inventory, then BookTitle).",
    )
    purge_cmd.add_argument("--dry-run", action="store_true", help="Show what would be deleted without committing.")
    purge_cmd.set_defaults(func=cmd_purge_null)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
