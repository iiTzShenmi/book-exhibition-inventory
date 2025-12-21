#!/usr/bin/env python
"""
Lightweight watchdog that runs key maintenance commands and reports status.

Default flow (no flags):
  - diagnose against Postgres (DATABASE_URL from .env)
  - diagnose against local SQLite (DATABASE_URL cleared, SKIP_INIT=1)
  - purge-null dry-run against both targets (reports what would be removed)

You can opt into sync/purge actions with flags.
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools import env_loader  # loads .env early


@dataclass
class Stage:
    name: str
    cmd: List[str]
    desc: str
    env: Dict[str, str]
    skip: bool = False


def build_env(**overrides: str) -> Dict[str, str]:
    env = os.environ.copy()
    env.update({k: v for k, v in overrides.items() if v is not None})
    return env


def run_stage(stage: Stage, verbose: bool) -> int:
    if stage.skip:
        print(f"[skip] {stage.name}: {stage.desc}")
        return 0
    print(f"[run] {stage.name}: {stage.desc}")
    proc = subprocess.run(
        stage.cmd,
        cwd=ROOT_DIR,
        env=stage.env,
        capture_output=True,
        text=True,
    )
    status = "OK" if proc.returncode == 0 else f"FAIL ({proc.returncode})"
    print(f"[done] {stage.name}: {status}")
    if verbose or proc.returncode != 0:
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode


def build_stages(args: argparse.Namespace) -> List[Stage]:
    stages: List[Stage] = []
    py = sys.executable

    run_cloud = not args.local_only
    run_local = not args.cloud_only

    if run_cloud:
        stages.append(
            Stage(
                name="cloud-diagnose",
                desc="diagnose Postgres (DATABASE_URL)",
                cmd=[py, "-m", "database.tools.db_tools", "diagnose"],
                env=build_env(),
            )
        )
        stages.append(
            Stage(
                name="cloud-purge-null",
                desc="purge-null on Postgres",
                cmd=[py, "-m", "database.tools.db_tools", "purge-null"]
                + ([] if args.apply_purge_cloud else ["--dry-run"]),
                env=build_env(),
                skip=args.skip_purge,
            )
        )

    if run_local:
        local_env = build_env(DATABASE_URL="", SKIP_INIT="1")
        stages.append(
            Stage(
                name="local-diagnose",
                desc="diagnose local SQLite (DATABASE_URL cleared)",
                cmd=[py, "-m", "database.tools.db_tools", "diagnose"],
                env=local_env,
            )
        )
        stages.append(
            Stage(
                name="local-purge-null",
                desc="purge-null on local SQLite",
                cmd=[py, "-m", "database.tools.db_tools", "purge-null"]
                + ([] if args.apply_purge_local else ["--dry-run"]),
                env=local_env,
                skip=args.skip_purge,
            )
        )
        stages.append(
            Stage(
                name="local-sync-csv",
                desc="sync CSV into local DB",
                cmd=[py, "-m", "database.tools.db_tools", "sync-csv"],
                env=local_env,
                skip=not args.sync_csv,
            )
        )

    return stages


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watchdog runner for DB maintenance flows.")
    parser.add_argument("--cloud-only", action="store_true", help="Only run against Postgres (DATABASE_URL).")
    parser.add_argument("--local-only", action="store_true", help="Only run against local SQLite.")
    parser.add_argument("--skip-purge", action="store_true", help="Skip purge-null stages.")
    parser.add_argument("--apply-purge-cloud", action="store_true", help="Run cloud purge-null without --dry-run.")
    parser.add_argument("--apply-purge-local", action="store_true", help="Run local purge-null without --dry-run.")
    parser.add_argument("--sync-csv", action="store_true", help="Also run sync-csv on local DB.")
    parser.add_argument("--verbose", action="store_true", help="Show stdout/stderr for all stages.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stages = build_stages(args)
    failures = 0
    for stage in stages:
        rc = run_stage(stage, verbose=args.verbose)
        if rc != 0:
            failures += 1
    print(f"[summary] completed {len(stages)} stages with {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
