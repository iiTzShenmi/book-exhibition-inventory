"""Restore archived inventory rows for cabinets.

Usage (repo root):
  python tools/restore_archived_cabinets.py --cabinet "社文 2" --cabinet "商業 1"
  python tools/restore_archived_cabinets.py --all
  python tools/restore_archived_cabinets.py --all --dry-run
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


def restore_for_cabinet(cabinet, *, dry_run=False):
    archived = Inventory.query.filter_by(cabinet_id=cabinet.id, status="archived").count()
    if archived == 0:
        return 0
    if dry_run:
        return archived
    Inventory.query.filter_by(cabinet_id=cabinet.id, status="archived").update(
        {"status": "active", "deleted_at": None},
        synchronize_session=False,
    )
    return archived


def main():
    parser = argparse.ArgumentParser(description="Restore archived inventory by cabinet")
    parser.add_argument("--cabinet", action="append", help="Cabinet name to restore (repeatable)")
    parser.add_argument("--all", action="store_true", help="Restore archived inventory for all cabinets")
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

        total_restored = 0
        for cab in targets:
            restored = restore_for_cabinet(cab, dry_run=args.dry_run)
            total_restored += restored
            print(f"[cabinet] {cab.name} -> archived rows {restored}")

        if args.dry_run:
            print(f"[done] dry-run. total archived rows: {total_restored}")
        else:
            db.session.commit()
            print(f"[done] restored rows: {total_restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
