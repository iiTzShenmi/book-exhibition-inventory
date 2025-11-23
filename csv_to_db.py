"""Utility script to sync the CSV file back into the database."""

from app import app, sync_csv_to_db, CSV_PATH


if __name__ == "__main__":
    with app.app_context():
        print(f"[csv_to_db] Syncing from {CSV_PATH}...")
        sync_csv_to_db()
