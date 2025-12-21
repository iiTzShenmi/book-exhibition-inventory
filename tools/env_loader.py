"""
Lightweight .env loader (key=value per line, no external deps).
Call load_env() early in scripts to populate os.environ.
"""

import os
from pathlib import Path


def load_env(path: str | None = None) -> None:
    env_path = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# Auto-load on import so scripts only need `from tools import env_loader`.
load_env()
