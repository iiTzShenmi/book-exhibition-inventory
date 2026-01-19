"""Set inventory in_stock to True for active rows.

Usage (repo root):
  python tools/restore_in_stock_true.py --all
  python tools/restore_in_stock_true.py --cabinet "社文 2" --cabinet "商業 1"
  python tools/restore_in_stock_true.py --all --dry-run
"""
import argparse
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools import env_loader  # noqa: F401  (loads .env into os.environ)

from app import app, db
from database.models import Cabinet, Inventory


def set_in_stock_true(cabinet=None, *, include_archived=False, dry_run=False):
    query = Inventory.query
    if cabinet is not None:
        query = query.filter(Inventory.cabinet_id == cabinet.id)
    if not include_archived:
        query = query.filter(Inventory.status == "active")
    query = query.filter(Inventory.in_stock.is_(False))
    count = query.count()
    if dry_run:
        return count
    query.update({"in_stock": True}, synchronize_session=False)
    return count


def main():
    parser = argparse.ArgumentParser(description="Restore in_stock=True for inventory rows")
    parser.add_argument("--cabinet", action="append", help="Cabinet name to update (repeatable)")
    parser.add_argument("--all", action="store_true", help="Update all cabinets")
    parser.add_argument("--include-archived", action="store_true", help="Also update archived inventory")
    parser.add_argument("--dry-run", action="store_true", help="Show counts without updating")
    args = parser.parse_args()

    if not args.all and not args.cabinet:
        print("[error] Provide --cabinet or --all")
        return 1

    with app.app_context():
        targets = []
        if args.all:
            targets = Cabinet.query.order_by(Cabinet.name).all()
        else:
            for name in args.cabinet:
                cab = Cabinet.query.filter_by(name=name).first()
                if not cab:
                    print(f"[warn] cabinet not found: {name}")
                    continue
                targets.append(cab)

        total_updated = 0
        for cab in targets:
            updated = set_in_stock_true(
                cab,
                include_archived=args.include_archived,
                dry_run=args.dry_run,
            )
            total_updated += updated
            print(f"[cabinet] {cab.name} -> set in_stock True {updated}")

        if args.dry_run:
            print(f"[done] dry-run. total rows: {total_updated}")
        else:
            db.session.commit()
            print(f"[done] updated rows: {total_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
