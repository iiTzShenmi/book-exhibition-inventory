"""Create an admin invite code and store it in the DB.

Usage (repo root):
  python tools/create_admin_code.py --memo "for Alice"
  python tools/create_admin_code.py --memo "for Alice" --sqlite-ok   # allow writing to local SQLite

Prints the generated code once so you can share it with the admin.
"""
import argparse
import os
import sys
from datetime import datetime
from urllib.parse import urlparse, urlunparse

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools import env_loader  # loads .env into os.environ

from app import (
    app,
    db,
    generate_invite_code,
    hash_invite_code,
    invite_code_lookup,
    invite_reference,
    ensure_admin_email_column,
    migrate_plaintext_invite_codes,
)
from database.models import AdminInvite


ALLOWED_ROLES = {"admin", "manager", "advance-admin"}


def masked_db(uri: str) -> str:
    """Hide password in DB URI for logging."""
    if not uri:
        return "sqlite://"
    try:
        parsed = urlparse(uri)
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        cred = ""
        if user:
            cred = user
            if parsed.password:
                cred += ":****"
        netloc = f"{cred}@{host}{port}" if cred else f"{host}{port}"
        return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return "masked-db-uri"


def main():
    print("\n[start] create admin invite code")
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")

    p = argparse.ArgumentParser(description="Create an admin invite code")
    p.add_argument("--memo", help="note for who/why this code is issued")
    p.add_argument(
        "--role",
        default="admin",
        help="role for the invite (default: admin, e.g. advance-admin)",
    )
    p.add_argument(
        "--sqlite-ok",
        action="store_true",
        help="Allow writing to the default SQLite DB if DATABASE_URL is not set.",
    )
    args = p.parse_args()

    if not os.environ.get("DATABASE_URL") and not args.sqlite_ok:
        print("[error] DATABASE_URL not set; refusing to write to local SQLite. Use --sqlite-ok to override.")
        return 1

    print(f"[info] Target DB: {masked_db(db_uri)}")

    if args.role not in ALLOWED_ROLES:
        print(f"[error] role must be one of: {', '.join(sorted(ALLOWED_ROLES))}")
        return 1

    code = generate_invite_code(14)
    with app.app_context():
        print("[step] ensuring tables exist...")
        db.create_all()
        ensure_admin_email_column()
        migrate_plaintext_invite_codes()
        print("[step] inserting invite into DB...")
        invite = AdminInvite(
            code=invite_reference(),
            code_hash=hash_invite_code(code),
            code_lookup=invite_code_lookup(code),
            memo=args.memo,
            role=args.role,
        )
        db.session.add(invite)
        db.session.commit()
        total = AdminInvite.query.count()
        latest = (
            AdminInvite.query.order_by(AdminInvite.id.desc()).first()
            if total
            else None
        )
    print("[done] invite stored. Share this code with the admin:")
    print(code)
    if total is not None:
        print(f"[info] admin_invite row count: {total}")
        if latest:
            print(f"[info] latest invite id={latest.id} role={latest.role} memo={latest.memo or '-'} created={latest.created_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
