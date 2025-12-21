#!/usr/bin/env python
"""
⚠️ DEPRECATED: This file is deprecated. Use `db_sync.py` instead.

Sync local SQLite database to PostgreSQL cloud database.

This tool orchestrates the workflow of validating and syncing data from local SQLite
to the production PostgreSQL database on Render.

NEW USAGE (recommended):
  python database/tools/db_sync.py push [--auto-fix] [--fill-metadata]
  python database/tools/db_sync.py diagnose [--local-only] [--cloud-only]
  python database/tools/db_sync.py clean [--purge-null] [--dedupe] [--auto-fix]

OLD USAGE (deprecated):
  python database/tools/local_db_sync.py push
  python database/tools/local_db_sync.py diagnose

Subcommands:
  push       Validate/fix local, optionally sync CSV and metadata, then upload to Postgres and recheck.
  diagnose   Run diagnose on cloud and/or local targets for a quick health report.
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools import env_loader  # noqa: F401  # load .env early


def build_env(local: bool = False) -> Dict[str, str]:
    env = os.environ.copy()
    if local:
        env.pop("DATABASE_URL", None)
        env["DATABASE_URL"] = ""
        env["SKIP_INIT"] = "1"
    else:
        # Force initialization on cloud runs (empty string is falsy for the SKIP_INIT check)
        env["SKIP_INIT"] = ""
    return env


@dataclass
class CmdResult:
    rc: int
    stdout: str
    stderr: str


def run_cmd(label: str, cmd: List[str], env: Optional[Dict[str, str]] = None, verbose: bool = False) -> CmdResult:
    print(f"[run] {label}: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    out = proc.stdout.strip()
    err = proc.stderr.strip()
    rc = proc.returncode
    status = "OK" if rc == 0 else f"FAIL ({rc})"
    print(f"[done] {label}: {status}")
    if verbose or rc != 0:
        if out:
            print(out)
        if err:
            print(err, file=sys.stderr)
    return CmdResult(rc, out, err)


def prompt_yes(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    reply = input(question + suffix).strip().lower()
    if not reply:
        return default
    return reply.startswith("y")


def run_diagnose(local: bool, verbose: bool) -> int:
    env = build_env(local=local)
    label = "diagnose-local" if local else "diagnose-cloud"
    cmd = [sys.executable, "-m", "database.tools.db_tools", "diagnose"]
    return run_cmd(label, cmd, env=env, verbose=verbose).rc


def run_purge_null(local: bool, apply: bool, verbose: bool) -> int:
    env = build_env(local=local)
    label = "purge-null-local" if local else "purge-null-cloud"
    cmd = [sys.executable, "-m", "database.tools.db_tools", "purge-null"]
    if not apply:
        cmd.append("--dry-run")
    return run_cmd(label, cmd, env=env, verbose=verbose).rc


def run_dedupe(local: bool, prompt: bool, verbose: bool) -> int:
    env = build_env(local=local)
    label = "dedupe-local" if local else "dedupe-cloud"
    cmd = [sys.executable, "-m", "database.tools.db_tools", "dedupe", "--fix-null-inventory"]
    if prompt:
        cmd.append("--prompt")
    return run_cmd(label, cmd, env=env, verbose=verbose).rc


def run_sync_csv(verbose: bool) -> int:
    env = build_env(local=True)
    cmd = [sys.executable, "-m", "database.tools.db_tools", "sync-csv"]
    return run_cmd("sync-csv-local", cmd, env=env, verbose=verbose).rc


def run_upload(verbose: bool) -> int:
    cmd = [sys.executable, "database/tools/local_db_upload.py"]
    return run_cmd("upload-local-to-cloud", cmd, env=build_env(local=False), verbose=verbose).rc


def run_metadata_fill(limit: int, verbose: bool) -> None:
    env = build_env(local=True)
    fetchers = [
        ("fetch-covers", [sys.executable, "tools/fetch_cover_url.py", "--limit", str(limit), "--drop-missing", "--force-drop-missing"]),
        ("fetch-authors", [sys.executable, "tools/fetch_author.py", "--limit", str(limit)]),
        ("fetch-topics", [sys.executable, "tools/fetch_topics.py", "--limit", str(limit)]),
    ]
    for label, cmd in fetchers:
        run_cmd(label, cmd, env=env, verbose=verbose)


def ensure_local_clean(args) -> bool:
    """Loop until local diagnose passes or the user aborts."""
    attempts = 0
    while True:
        attempts += 1
        rc = run_diagnose(local=True, verbose=args.verbose)
        if rc == 0:
            return True
        if attempts > 3:
            print("[abort] diagnose still failing after 3 attempts.")
            return False

        if args.auto_fix or prompt_yes("Local diagnose failed. Run purge-null now?", default=True):
            run_purge_null(local=True, apply=True, verbose=args.verbose)
        if args.auto_fix or prompt_yes("Run dedupe now (interactive keepers)?", default=True):
            run_dedupe(local=True, prompt=True, verbose=args.verbose)
        # Re-loop and recheck


def cmd_push(args) -> int:
    if not args.no_sync_csv and Path("database/inventory.csv").exists():
        rc = run_sync_csv(verbose=args.verbose)
        if rc != 0:
            return rc

    if not ensure_local_clean(args):
        return 1

    if args.fill_metadata:
        run_metadata_fill(limit=args.fetch_limit, verbose=args.verbose)
        if not ensure_local_clean(args):
            return 1

    rc = run_upload(verbose=args.verbose)
    if rc != 0:
        return rc

    if not args.skip_cloud_diagnose:
        return run_diagnose(local=False, verbose=args.verbose)
    return 0


def cmd_diagnose(args) -> int:
    failures = 0
    if not args.local_only:
        failures += run_diagnose(local=False, verbose=args.verbose)
    if not args.cloud_only:
        failures += run_diagnose(local=True, verbose=args.verbose)
    return 0 if failures == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync local SQLite database to PostgreSQL cloud database.")
    sub = parser.add_subparsers(dest="command", required=True)

    push = sub.add_parser("push", help="Validate local, optionally fill metadata, upload to Postgres, recheck.")
    push.add_argument("--no-sync-csv", action="store_true", help="Skip syncing CSV into local DB before checks.")
    push.add_argument("--fill-metadata", action="store_true", help="Run cover/author/topic fetchers on local before upload.")
    push.add_argument("--fetch-limit", type=int, default=50, help="Limit per fetcher when filling metadata.")
    push.add_argument("--skip-cloud-diagnose", action="store_true", help="Skip final cloud diagnose after upload.")
    push.add_argument("--auto-fix", action="store_true", help="Auto-run purge-null and dedupe when diagnose fails.")
    push.add_argument("--verbose", action="store_true", help="Show stdout/stderr for all stages.")
    push.set_defaults(func=cmd_push)

    diag = sub.add_parser("diagnose", help="Run diagnose on cloud and/or local.")
    diag.add_argument("--cloud-only", action="store_true", help="Only diagnose cloud.")
    diag.add_argument("--local-only", action="store_true", help="Only diagnose local.")
    diag.add_argument("--verbose", action="store_true", help="Show stdout/stderr for all stages.")
    diag.set_defaults(func=cmd_diagnose)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    # Normalize mutually exclusive flags
    if getattr(args, "cloud_only", False):
        args.local_only = False
    if getattr(args, "local_only", False):
        args.cloud_only = False
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

