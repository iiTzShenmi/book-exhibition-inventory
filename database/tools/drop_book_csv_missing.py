"""
Drop titles listed in a file (default: database/titles_not_in_csv.txt).

Usage examples (run from repo root):
  python database/tools/drop_book_csv_missing.py
  python database/tools/drop_book_csv_missing.py --file database/tools/book_csv_missing.txt
  python database/tools/drop_book_csv_missing.py --force   # delete even if inventory > 0
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app  # noqa: E402
from database.models import db, BookTitle, Inventory  # noqa: E402


def load_titles(path):
    titles = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            title = line.split("\t")[0].strip()
            if title:
                titles.append(title)
    return titles


def drop_titles(titles, *, force=False):
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
        total_qty = sum((inv.qty_on_hand or 0) + (inv.qty_reserved or 0) for inv in inventories)
        if total_qty > 0 and not force:
            skipped += 1
            print(f"[skip] has stock ({total_qty}) :: {title}")
            continue

        for inv in inventories:
            db.session.delete(inv)
        db.session.delete(title_obj)
        removed += 1
        print(f"[delete] {title}")

    db.session.commit()
    return removed, skipped, missing


def main():
    parser = argparse.ArgumentParser(description="Drop titles listed in a file.")
    parser.add_argument(
        "--file",
        default=os.path.join(SCRIPT_DIR, "book_csv_missing.txt"),
        help="Path to title list (one title per line; first column is title if tab-separated).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force delete even if inventory quantities are > 0.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"[error] file not found: {args.file}")
        return 1

    titles = load_titles(args.file)
    if not titles:
        print("[info] no titles to process.")
        return 0

    with app.app_context():
        removed, skipped, missing = drop_titles(titles, force=args.force)

    try:
        with open(args.file, "w", encoding="utf-8") as f:
            f.write("")
        print(f"[info] cleared list file: {args.file}")
    except Exception as exc:
        print(f"[warn] failed to clear list file {args.file}: {exc}")

    print(f"[done] removed={removed}, skipped={skipped}, not_found={missing}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
