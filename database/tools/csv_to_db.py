"""Utility script to sync the CSV file back into the database."""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app, sync_csv_to_db, CSV_PATH  # noqa: E402


if __name__ == "__main__":
    with app.app_context():
        print(f"[csv_to_db] Syncing from {CSV_PATH}...")
        sync_csv_to_db()
