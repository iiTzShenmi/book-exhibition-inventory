"""
Download a Render Postgres database to a local dump file using pg_dump.

Usage:
  python database/tools/cloud_db_download.py
  python database/tools/cloud_db_download.py --output database/backups/render_dump.sql
"""

import argparse
import os
import subprocess
from datetime import datetime
from pathlib import Path
import sys
from urllib.parse import urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import env_loader  # loads .env into os.environ


def resolve_output_path(user_path: str | None) -> Path:
    if user_path:
        return Path(user_path).expanduser().resolve()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    default_dir = Path(__file__).resolve().parents[1] / "backups"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir / f"render_postgres_dump_{timestamp}.sql"


def mask_uri(uri: str) -> str:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Download cloud Postgres to a local dump via pg_dump.")
    parser.add_argument("--output", help="Path to write the dump file (default: database/backups/<timestamp>.sql)")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[error] DATABASE_URL is not set; nothing to download.")
        return 1
    if not db_url.startswith(("postgres://", "postgresql://")):
        print(f"[error] DATABASE_URL does not look like Postgres: {mask_uri(db_url)}")
        return 1

    output_path = resolve_output_path(args.output)
    print(f"[download] target: {output_path}")

    # Use pg_dump for a plain SQL dump; Render provides this on the shell instance.
    cmd = ["pg_dump", db_url, "-f", str(output_path)]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("[error] pg_dump is not installed or not on PATH.")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"[error] pg_dump failed with code {exc.returncode}")
        return exc.returncode or 1

    size = output_path.stat().st_size if output_path.exists() else 0
    print(f"[done] wrote {size} bytes to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
