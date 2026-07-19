import csv
import os
import secrets
import re
import shutil
import json
import io
import hashlib
import hmac
import urllib.parse
import urllib.request
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from sqlalchemy import text, func
from flask import Flask, abort, jsonify, request, session
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
import redis
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from tools import env_loader  # loads .env into os.environ
from database.models import TopSellerSnapshot, db, Book, Cabinet, BookTitle, Inventory, AuditLog, AdminUser, AdminInvite
from database.models import EventSchedule, BackupArchive, event_books
from similarity import BookProfile, suggest_for_missing_title, parse_topics_field

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
APP_VERSION = "2.3.1"
DATA_DIR = os.path.join(BASE_DIR, "database")
os.makedirs(DATA_DIR, exist_ok=True)
CSV_PATH = os.path.join(DATA_DIR, "inventory.csv")
DB_PATH = os.path.join(DATA_DIR, "inventory.db")
# AUTO_GIT_PUSH removed - unused variable
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
LAST_BACKUP_META = os.path.join(BACKUP_DIR, "last_auto_backup.json")

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_RAW = os.environ.get("ADMIN_PASSWORD")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def env_flag_any(names: tuple[str, ...], default: bool = False) -> bool:
    """Read the first configured boolean env flag from a list of aliases."""
    for name in names:
        if os.environ.get(name) is not None:
            return env_flag(name, default)
    return default


def mask_sensitive_uri(uri: str | None) -> str:
    if not uri:
        return ""
    try:
        parsed = urllib.parse.urlparse(uri)
        if not parsed.password:
            return uri
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{user}:****@{host}{port}" if user else f"{host}{port}"
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return "<masked>"


IS_HOSTED_DEPLOY = (
    os.environ.get("FLASK_ENV") == "production"
    or os.environ.get("APP_ENV") == "production"
    or env_flag("RENDER")
    or bool(os.environ.get("RENDER_SERVICE_ID"))
    or bool(os.environ.get("RENDER_EXTERNAL_HOSTNAME"))
)
IS_PRODUCTION = IS_HOSTED_DEPLOY or bool(DATABASE_URL)
IS_TESTING_ENV = os.environ.get("APP_ENV") == "testing" or os.environ.get("FLASK_ENV") == "testing"
STRICT_HOSTED_PRODUCTION = IS_HOSTED_DEPLOY and not IS_TESTING_ENV

CONFIGURED_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("APP_SECRET_KEY")

if STRICT_HOSTED_PRODUCTION and not CONFIGURED_SECRET_KEY:
    raise RuntimeError("FLASK_SECRET_KEY or APP_SECRET_KEY is required in hosted production")

if STRICT_HOSTED_PRODUCTION and not os.environ.get("INVITE_CODE_PEPPER"):
    raise RuntimeError("INVITE_CODE_PEPPER is required in hosted production")

if STRICT_HOSTED_PRODUCTION and ADMIN_PASSWORD_RAW:
    raise RuntimeError("Use ADMIN_PASSWORD_HASH instead of plaintext ADMIN_PASSWORD in hosted production")

if not ADMIN_PASSWORD_HASH and not ADMIN_PASSWORD_RAW:
    if not IS_PRODUCTION:
        ADMIN_PASSWORD_RAW = secrets.token_urlsafe(12)
        print(f"[dev] Generated one-time ADMIN_PASSWORD for this run: {ADMIN_PASSWORD_RAW}")

if not ADMIN_PASSWORD_HASH and ADMIN_PASSWORD_RAW:
    ADMIN_PASSWORD_HASH = generate_password_hash(ADMIN_PASSWORD_RAW)
DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")

app = Flask(__name__)
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
app.config["APP_VERSION"] = APP_VERSION
app.config["STATIC_VERSION"] = os.environ.get("STATIC_VERSION") or datetime.utcnow().strftime("%Y%m%d%H%M%S")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.secret_key = (
    CONFIGURED_SECRET_KEY
    or secrets.token_hex(32)
)
if IS_HOSTED_DEPLOY:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Session configuration for security
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
app.config["SESSION_COOKIE_SECURE"] = env_flag("SESSION_COOKIE_SECURE", IS_HOSTED_DEPLOY)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = env_int("MAX_UPLOAD_MB", 5) * 1024 * 1024
trusted_hosts = [
    host.strip()
    for host in (
        os.environ.get("TRUSTED_HOSTS")
        or os.environ.get("EXIS_TRUSTED_HOSTS")
        or ""
    ).split(",")
    if host.strip()
]
render_hostname = (os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "").strip()
if render_hostname and render_hostname not in trusted_hosts:
    trusted_hosts.append(render_hostname)
if trusted_hosts:
    app.config["TRUSTED_HOSTS"] = trusted_hosts

REDIS_URL = os.environ.get("REDIS_URL")
REQUIRE_REDIS_RATE_LIMIT = (
    env_flag("EXIS_REQUIRE_REDIS", IS_HOSTED_DEPLOY)
    and not env_flag("EXIS_ALLOW_MEMORY_RATE_LIMIT", False)
)
redis_client = None
_local_cache: dict[str, tuple[datetime, object]] = {}

if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL)
        redis_client.ping()
        print(f"[cache] connected to redis at {mask_sensitive_uri(REDIS_URL)}")
    except Exception as err:
        if REQUIRE_REDIS_RATE_LIMIT:
            raise RuntimeError("REDIS_URL is required and must be reachable for hosted rate limiting") from err
        print(f"[cache] redis connection failed ({err}); falling back to in-process cache")
elif REQUIRE_REDIS_RATE_LIMIT:
    raise RuntimeError(
        "REDIS_URL is required for hosted production rate limiting. "
        "Set EXIS_ALLOW_MEMORY_RATE_LIMIT=1 only for a single-process emergency fallback."
    )
elif IS_HOSTED_DEPLOY:
    print("[cache] WARNING: REDIS_URL is not set; rate limiting uses per-process memory storage")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=REDIS_URL if redis_client else "memory://",
    default_limits=[os.environ.get("RATELIMIT_DEFAULT", "300 per minute")],
    strategy=os.environ.get("RATELIMIT_STRATEGY", "moving-window"),
)
limiter.init_app(app)

db.init_app(app)


def masked_db_uri(uri: str | None) -> str:
    """Return a masked DB URI (hide password)."""
    if not uri:
        return "sqlite://"
    try:
        parsed = urllib.parse.urlparse(uri)
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        cred = ""
        if user:
            cred = user
            if parsed.password:
                cred += ":****"
        netloc = f"{cred}@{host}{port}" if cred else f"{host}{port}"
        masked = parsed._replace(netloc=netloc)
        return urllib.parse.urlunparse(masked)
    except Exception:
        return "masked-db-uri"

def parse_qty(value):
    """Parse a quantity string that may come as bool-ish text or int."""
    if value is None:
        return 0
    text_val = str(value).strip().lower()
    if text_val in {"true", "yes", "y", "1"}:
        return 1
    try:
        return max(int(text_val), 0)
    except ValueError:
        return 0


CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def csv_safe_cell(value) -> str:
    """Prefix spreadsheet-formula-looking cells before writing human-opened CSVs."""
    text_value = "" if value is None else str(value)
    text_value = text_value.replace("\r", " ").replace("\n", " ")
    if text_value.lstrip(" \t\r\n").startswith(CSV_FORMULA_PREFIXES):
        return f"\t{text_value}"
    return text_value


def csv_safe_row(values):
    return [csv_safe_cell(value) for value in values]


def pg_env_from_database_url(database_url: str) -> dict[str, str]:
    """Build libpq env vars so pg_dump does not receive credentials in argv."""
    parsed = urllib.parse.urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must be a Postgres URL")

    env = os.environ.copy()
    if parsed.hostname:
        env["PGHOST"] = parsed.hostname
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = urllib.parse.unquote(parsed.password)
    db_name = (parsed.path or "").lstrip("/")
    if db_name:
        env["PGDATABASE"] = urllib.parse.unquote(db_name)
    query = urllib.parse.parse_qs(parsed.query or "")
    if query.get("sslmode"):
        env["PGSSLMODE"] = query["sslmode"][-1]
    return env


def run_pg_dump_to_file(database_url: str, output_path: str) -> None:
    """Write a Postgres dump without exposing the full URL in process arguments."""
    subprocess.run(
        ["pg_dump", "-f", output_path],
        check=True,
        env=pg_env_from_database_url(database_url),
    )


def sync_csv_to_db(
    csv_text: str | None = None,
    csv_path: str | None = None,
    force: bool = False,
    remove_missing: bool = True,
):
    """Import or update the database from CSV (one-way).

    CSV columns supported:
    - cabinet_name, title, qty_or_bool, author (optional)
    """
    summary = {
        "rows": 0,
        "pairs": 0,
        "created_inventory": 0,
        "archived_inventory": 0,
    }
    # Skip when running against remote DB unless explicitly enabled
    # Check ENABLE_CSV_SYNC - treat "1", "true", "yes" as enabled
    enable_sync = os.environ.get("ENABLE_CSV_SYNC", "").strip().lower()
    if is_postgres() and enable_sync not in ("1", "true", "yes") and not force:
        print("[sync_csv_to_db] skipped (remote DB detected; set ENABLE_CSV_SYNC=1 to allow)")
        return summary
    
    # Skip if SKIP_INIT is set and database already has data
    skip_init = os.environ.get("SKIP_INIT", "").strip().lower()
    if skip_init and skip_init not in ("0", "false", "no") and not force:
        # Check if database already has data
        if Inventory.query.first() is not None:
            print("[sync_csv_to_db] skipped (SKIP_INIT set and DB has data)")
            return summary
    
    if csv_text is None:
        csv_path = csv_path or CSV_PATH
        if not os.path.exists(csv_path):
            print(f"[sync_csv_to_db] CSV not found: {csv_path}")
            return summary
    
    # Ensure quantity columns are removed before syncing (migration)
    # This is needed even when SKIP_INIT is set
    drop_quantity_columns_from_sqlite()

    def normalize_title(raw):
        # Simple normalization: strip and collapse internal whitespace
        return re.sub(r"\s+", " ", (raw or "").strip())

    aggregates = Counter()  # (cabinet_name, title) -> qty
    explicit_qty_flags = {}
    authors = {}
    csv_titles = set()

    def is_header(row):
        if len(row) < 2:
            return False
        first = row[0].strip().lstrip("\ufeff").lower()
        second = row[1].strip().lower()
        return first in {"cabinet", "cabinet_name"} and second in {"title", "book", "book_title"}

    if csv_text is None:
        csv_source = open(csv_path, "r", encoding="utf-8")
    else:
        csv_source = io.StringIO(csv_text)

    with csv_source as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 2:
                continue
            if is_header(row):
                continue
            cab_name, title, *rest = row
            qty_raw = rest[0] if len(rest) >= 1 else None
            rest = rest[1:] if len(rest) >= 2 else []
            cab_name = cab_name.lstrip("\ufeff")
            title = title.lstrip("\ufeff")
            summary["rows"] += 1
            csv_titles.add(normalize_title(title))
            author = (rest[0].strip() if rest else "") or None
            qty_raw_str = "" if qty_raw is None else str(qty_raw).strip()
            has_explicit_qty = qty_raw_str != ""
            qty = parse_qty(qty_raw) if has_explicit_qty else 1
            key = (cab_name.strip(), title.strip())
            aggregates[key] += qty
            if has_explicit_qty:
                explicit_qty_flags[key] = True
            if author and title not in authors:
                authors[title] = author

    summary["pairs"] = len(aggregates)

    seen_pairs = set()
    for (cab_name, title), qty in aggregates.items():
        if not cab_name or not title:
            continue
        seen_pairs.add((cab_name, title))
        cabinet = Cabinet.query.filter_by(name=cab_name).first()
        if not cabinet:
            cabinet = Cabinet(name=cab_name)
            db.session.add(cabinet)
            db.session.flush()
        if hasattr(cabinet, "type") and not cabinet.type:
            cabinet.type = "display"

        title_obj = get_or_create_title(title, authors.get(title))
        # Defensive check: ensure title_obj has valid id
        if not title_obj or not title_obj.id:
            print(f"[sync_csv_to_db][error] Failed to get/create title '{title}'")
            continue
            
        inventory = Inventory.query.filter_by(
            title_id=title_obj.id, cabinet_id=cabinet.id
        ).first()
        if not inventory:
            # Defensive check before creating Inventory
            if not title_obj.id or not cabinet.id:
                print(f"[sync_csv_to_db][error] Cannot create inventory: title_id={title_obj.id}, cabinet_id={cabinet.id}")
                continue
            inventory = Inventory(
                title_id=title_obj.id,
                cabinet_id=cabinet.id,
            )
            db.session.add(inventory)
            summary["created_inventory"] += 1
        if inventory.status != "active":
            inventory.status = "active"
            inventory.deleted_at = None
        if explicit_qty_flags.get((cab_name, title)):
            inventory.in_stock = qty > 0
        elif inventory.id is None:
            inventory.in_stock = True

    if remove_missing:
        # Remove inventory rows no longer present (optimized: only query needed columns)
        # Use a more efficient query that only fetches what we need
        existing_inventory = db.session.query(
            Inventory.id,
            Inventory.cabinet_id,
            Inventory.title_id,
            Cabinet.name.label('cabinet_name'),
            BookTitle.title.label('book_title')
        ).join(Cabinet).join(BookTitle).filter(Inventory.status == "active").all()
        
        for item in existing_inventory:
            pair = (item.cabinet_name or "", item.book_title or "")
            if pair not in seen_pairs:
                inv = Inventory.query.get(item.id)
                if inv:
                    inv.status = "archived"
                    inv.deleted_at = datetime.utcnow()
                    inv.in_stock = False
                    summary["archived_inventory"] += 1

    db.session.commit()
    print("[sync_csv_to_db] CSV -> DB sync complete.")
    return summary

    if remove_missing:
        # Report DB titles not present in CSV (potential renames/duplicates)
        # Optimized: only check titles that have inventory entries
        if csv_titles:
            missing_titles = []
            # Only query titles that are actually in inventory
            titles_with_inventory = (
                db.session.query(BookTitle)
                .join(Inventory)
                .filter(Inventory.status == "active")
                .distinct()
                .all()
            )
            for title_obj in titles_with_inventory:
                norm = normalize_title(title_obj.title)
                if norm and norm not in csv_titles:
                    count = Inventory.query.filter_by(title_id=title_obj.id, status="active").count()
                    missing_titles.append((title_obj.title, count))
            if missing_titles:
                logs_dir = os.path.join(DATA_DIR, "logs")
                os.makedirs(logs_dir, exist_ok=True)
                log_path = os.path.join(logs_dir, "book_csv_missing.txt")
                with open(log_path, "w", encoding="utf-8") as f:
                    for title, count in missing_titles:
                        f.write(f"{title}\tinventory_count={count}\n")
                print(f"[sync_csv_to_db] Titles present in DB but missing in CSV: {len(missing_titles)}")
                print(f"[sync_csv_to_db] See details in {log_path}")


def export_db_to_csv():
    """Export database back to CSV (one-way).
    
    NOTE: This function is for one-time exports only.
    In production, PostgreSQL is the single source of truth.
    CSV exports should only be used for:
    - Backups
    - Manual exports
    - Migration purposes
    
    This function is NOT called automatically in production.
    """
    # Skip CSV export in production unless explicitly enabled
    if is_postgres() and not os.environ.get("ENABLE_CSV_EXPORT"):
        print("[export_db_to_csv] skipped (production mode; set ENABLE_CSV_EXPORT=1 to enable)")
        return
    
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for cab in Cabinet.query.all():
            for inv in cab.books:
                if getattr(inv, "status", "active") != "active":
                    continue
                writer.writerow(csv_safe_row([
                    cab.name,
                    inv.title,
                    "True" if inv.in_stock else "False",
                    inv.author or "",
                ]))
    print("[export_db_to_csv] DB -> CSV export complete.")


def create_backup():
    """Create timestamped backups (Postgres: pg_dump; SQLite: file copy) plus optional CSV."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_backup = None
    
    # Only export CSV for backups if explicitly enabled or in development
    if not is_postgres() or os.environ.get("ENABLE_CSV_EXPORT"):
        export_db_to_csv()
        if os.path.exists(CSV_PATH):
            csv_backup = os.path.join(BACKUP_DIR, f"inventory_{ts}.csv")
            shutil.copy2(CSV_PATH, csv_backup)

    if is_postgres() and DATABASE_URL:
        dump_path = os.path.join(BACKUP_DIR, f"inventory_{ts}.sql")
        try:
            run_pg_dump_to_file(DATABASE_URL, dump_path)
            print(f"[backup] pg_dump saved to {dump_path}")
            return {"db": dump_path, "csv": csv_backup, "timestamp": ts}
        except Exception as exc:
            print(f"[backup] pg_dump failed: {exc}")
            return {"db": None, "csv": csv_backup, "timestamp": ts, "error": str(exc)}
    else:
        db_backup = os.path.join(BACKUP_DIR, f"inventory_{ts}.db")
        shutil.copy2(DB_PATH, db_backup)
        # Always include CSV backup for SQLite (development)
        if csv_backup is None and os.path.exists(CSV_PATH):
            csv_backup = os.path.join(BACKUP_DIR, f"inventory_{ts}.csv")
            shutil.copy2(CSV_PATH, csv_backup)
        return {"db": db_backup, "csv": csv_backup, "timestamp": ts}


def ensure_hourly_backup():
    """Ensure at least one backup per hour; lightweight guard on admin pages."""
    now = datetime.utcnow()
    last_ts = None
    if os.path.exists(LAST_BACKUP_META):
        try:
            with open(LAST_BACKUP_META, "r", encoding="utf-8") as f:
                data = json.load(f)
                last_ts = datetime.fromisoformat(data.get("last") or "")
        except Exception:
            last_ts = None
    if last_ts and (now - last_ts).total_seconds() < 3600:
        return None
    backups = create_backup()
    with open(LAST_BACKUP_META, "w", encoding="utf-8") as f:
        json.dump({"last": now.isoformat()}, f)
    log_action("auto_backup", target="system", details=f"db={os.path.basename(backups['db'])}")
    db.session.commit()
    return backups


def drop_quantity_columns_from_sqlite():
    """Drop qty_on_hand and qty_reserved columns from SQLite inventory table.
    
    SQLite doesn't support DROP COLUMN directly, so we recreate the table.
    """
    if is_postgres():
        # For PostgreSQL, use ALTER TABLE DROP COLUMN
        with db.engine.connect() as conn:
            # Check if columns exist
            check = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'inventory' 
                AND column_name IN ('qty_on_hand', 'qty_reserved')
            """)
            existing = [row[0] for row in conn.execute(check).fetchall()]
            if existing:
                with conn.begin():
                    for col in existing:
                        conn.execute(text(f"ALTER TABLE inventory DROP COLUMN IF EXISTS {col}"))
                print(f"[migration] Dropped quantity columns from PostgreSQL: {existing}")
            else:
                print("[migration] Quantity columns already removed from PostgreSQL")
        return

    # Standardize legacy state columns before reconstructing the table so the
    # preservation query can remain static and independently auditable.
    ensure_inventory_in_stock_column()
    ensure_inventory_status_columns()

    # SQLite: recreate table without quantity columns
    # Check if columns exist first
    with db.engine.connect() as check_conn:
        result = check_conn.execute(text("PRAGMA table_info(inventory)"))
        columns = {row[1]: row for row in result}
        
        has_qty_columns = "qty_on_hand" in columns or "qty_reserved" in columns
        if not has_qty_columns:
            print("[migration] Quantity columns already removed from SQLite")
            return  # Already migrated
    
    print("[migration] Recreating inventory table without quantity columns...")
    
    # Read the complete non-quantity state before replacing the table.
    select_sql = text("""
        SELECT id, title_id, cabinet_id,
               COALESCE(in_stock, 1) AS in_stock,
               COALESCE(status, 'active') AS status,
               deleted_at, created_at, updated_at
        FROM inventory
    """)
    try:
        with db.engine.connect() as read_conn:
            data = [dict(row._mapping) for row in read_conn.execute(select_sql).fetchall()]
    except Exception as e:
        print(f"[migration] Error reading inventory data: {e}")
        raise RuntimeError("Cannot safely migrate inventory quantity columns") from e

    candidate_exists = None
    with db.engine.connect() as check_conn:
        candidate_exists = check_conn.execute(text("""
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                  'inventory_quantity_migration_candidate',
                  'inventory_quantity_migration_legacy'
              )
        """)).fetchone()
    if candidate_exists:
        raise RuntimeError(
            "A previous inventory quantity migration requires manual review before retrying"
        )

    create_candidate_sql = text("""
        CREATE TABLE inventory_quantity_migration_candidate (
            id INTEGER NOT NULL,
            title_id INTEGER NOT NULL,
            cabinet_id INTEGER NOT NULL,
            in_stock BOOLEAN NOT NULL,
            status VARCHAR(20) NOT NULL,
            deleted_at DATETIME,
            created_at DATETIME,
            updated_at DATETIME,
            PRIMARY KEY (id),
            CONSTRAINT uq_inventory_title_cabinet UNIQUE (title_id, cabinet_id),
            FOREIGN KEY(title_id) REFERENCES book_title (id),
            FOREIGN KEY(cabinet_id) REFERENCES cabinet (id)
        )
    """)
    insert_candidate_sql = text("""
        INSERT INTO inventory_quantity_migration_candidate (
            id, title_id, cabinet_id, in_stock, status, deleted_at, created_at, updated_at
        ) VALUES (
            :id, :title_id, :cabinet_id, :in_stock, :status, :deleted_at, :created_at, :updated_at
        )
    """)
    try:
        # Build and validate a separate table before touching live inventory.
        with db.engine.begin() as trans_conn:
            trans_conn.execute(create_candidate_sql)
            if data:
                trans_conn.execute(insert_candidate_sql, data)
            restored_count = trans_conn.execute(
                text("SELECT COUNT(*) FROM inventory_quantity_migration_candidate")
            ).scalar_one()
            if restored_count != len(data):
                raise RuntimeError(
                    f"Inventory migration restored {restored_count} of {len(data)} rows"
                )
    except Exception as e:
        with db.engine.begin() as cleanup_conn:
            cleanup_conn.execute(text("DROP TABLE IF EXISTS inventory_quantity_migration_candidate"))
        print(f"[migration] Candidate inventory migration failed safely: {e}")
        raise

    try:
        # The candidate contains every row and enforces the current schema.
        # Only now replace the original table and then restore its status index.
        with db.engine.begin() as swap_conn:
            swap_conn.execute(text("ALTER TABLE inventory RENAME TO inventory_quantity_migration_legacy"))
            swap_conn.execute(text("ALTER TABLE inventory_quantity_migration_candidate RENAME TO inventory"))
            restored_count = swap_conn.execute(text("SELECT COUNT(*) FROM inventory")).scalar_one()
            if restored_count != len(data):
                raise RuntimeError(
                    f"Inventory migration swap restored {restored_count} of {len(data)} rows"
                )
        with db.engine.begin() as cleanup_conn:
            cleanup_conn.execute(text("DROP TABLE inventory_quantity_migration_legacy"))
            cleanup_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_status ON inventory (status)"))
    except Exception as e:
        # If the first rename succeeded but the candidate rename did not, put
        # the original table back so the application remains operable.
        with db.engine.begin() as recovery_conn:
            tables = {
                row[0]
                for row in recovery_conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
            if (
                "inventory" not in tables
                and "inventory_quantity_migration_legacy" in tables
            ):
                recovery_conn.execute(
                    text("ALTER TABLE inventory_quantity_migration_legacy RENAME TO inventory")
                )
                if "inventory_quantity_migration_candidate" in tables:
                    recovery_conn.execute(
                        text("DROP TABLE inventory_quantity_migration_candidate")
                    )
        print(f"[migration] Inventory candidate swap requires recovery: {e}")
        raise
    
    # Reset sequence if needed
    try:
        with db.engine.begin() as seq_conn:
            max_id = seq_conn.execute(text("SELECT MAX(id) FROM inventory")).scalar() or 0
            if max_id:
                seq_conn.execute(
                    text("UPDATE sqlite_sequence SET seq = :max_id WHERE name = 'inventory'"),
                    {"max_id": max_id},
                )
    except Exception:
        pass  # Sequence might not exist yet
    
    print("[migration] Successfully removed quantity columns from SQLite inventory table")


def ensure_cabinet_type_column():
    """Ensure cabinet table has a type column for main/reserve tagging."""
    if is_postgres():
        return  # schema already includes type; PRAGMA not supported
    with db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(cabinet)"))
        columns = [row[1] for row in result]
    if "type" not in columns:
        with db.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE cabinet ADD COLUMN type VARCHAR(10) DEFAULT 'display'")
            )
            conn.execute(
                text("UPDATE cabinet SET type = 'display' WHERE type IS NULL")
            )


def ensure_author_column():
    """Ensure the legacy book table has an author column (for migration)."""
    if is_postgres():
        return  # legacy sqlite-only migration
    with db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(book)"))
        columns = [row[1] for row in result]
    if "author" not in columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE book ADD COLUMN author TEXT"))


def ensure_title_cover_column():
    """Ensure BookTitle has a cover_link column for cover lookups."""
    inspector = db.inspect(db.engine)
    if "book_title" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("book_title")}
    if "cover_link" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE book_title ADD COLUMN cover_link TEXT"))


def ensure_title_topics_column():
    """Ensure legacy BookTitle tables support topic-based search data."""
    inspector = db.inspect(db.engine)
    if "book_title" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("book_title")}
    if "topics" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE book_title ADD COLUMN topics TEXT"))


def ensure_book_title_view_columns():
    """Ensure BookTitle has view_count and last_viewed_at columns."""
    if is_postgres():
        try:
            with db.engine.connect() as check_conn:
                view_col = check_conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'book_title'
                    AND column_name = 'view_count'
                """)).fetchone()
                last_col = check_conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'book_title'
                    AND column_name = 'last_viewed_at'
                """)).fetchone()
            if not view_col:
                with db.engine.begin() as alter_conn:
                    alter_conn.execute(text("ALTER TABLE book_title ADD COLUMN view_count INTEGER DEFAULT 0 NOT NULL"))
                    alter_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_book_title_view_count ON book_title (view_count)"))
            if not last_col:
                with db.engine.begin() as alter_conn:
                    alter_conn.execute(text("ALTER TABLE book_title ADD COLUMN last_viewed_at TIMESTAMP NULL"))
        except Exception as e:
            print(f"[migration] Error ensuring book_title view columns: {e}")
    else:
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(book_title)"))
                columns = [row[1] for row in result]
            with db.engine.begin() as conn:
                if "view_count" not in columns:
                    conn.execute(text("ALTER TABLE book_title ADD COLUMN view_count INTEGER DEFAULT 0 NOT NULL"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_book_title_view_count ON book_title (view_count)"))
                if "last_viewed_at" not in columns:
                    conn.execute(text("ALTER TABLE book_title ADD COLUMN last_viewed_at DATETIME NULL"))
        except Exception as e:
            print(f"[migration] Error ensuring book_title view columns (sqlite): {e}")

def ensure_event_date_columns():
    """Ensure event_schedule has date_start/date_end columns."""
    try:
        inspector = db.inspect(db.engine)
        if "event_schedule" not in inspector.get_table_names():
            return
        existing = {col["name"] for col in inspector.get_columns("event_schedule")}
        to_add = []
        if "date_start" not in existing:
            to_add.append("date_start")
        if "date_end" not in existing:
            to_add.append("date_end")
        if not to_add:
            return

        if is_postgres():
            with db.engine.begin() as conn:
                for col in to_add:
                    conn.execute(db.text(f"ALTER TABLE event_schedule ADD COLUMN {col} DATE"))
            print(f"[migration] Added event_schedule columns: {', '.join(to_add)}")
        else:
            conn = db.engine.raw_connection()
            cur = conn.cursor()
            for col in to_add:
                cur.execute(f"ALTER TABLE event_schedule ADD COLUMN {col} DATE")
            conn.commit()
            conn.close()
            print(f"[migration] Added event_schedule columns (sqlite): {', '.join(to_add)}")
    except Exception as e:
        print(f"[migration] Error ensuring event_schedule date columns: {e}")

def ensure_inventory_in_stock_column():
    """Ensure Inventory table has an in_stock column for toggle functionality."""
    if is_postgres():
        # Check if column exists
        try:
            with db.engine.connect() as check_conn:
                check = text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'inventory' 
                    AND column_name = 'in_stock'
                """)
                existing = check_conn.execute(check).fetchone()
                if not existing:
                    print("[migration] Adding in_stock column to PostgreSQL inventory table...")
                    # Use a separate connection for the ALTER TABLE (must be in autocommit mode)
                    with db.engine.begin() as alter_conn:
                        alter_conn.execute(text("ALTER TABLE inventory ADD COLUMN in_stock BOOLEAN DEFAULT TRUE NOT NULL"))
                        # Update is not needed since DEFAULT TRUE handles it, but let's be safe
                        alter_conn.execute(text("UPDATE inventory SET in_stock = TRUE WHERE in_stock IS NULL"))
                    print("[migration] Successfully added in_stock column to PostgreSQL")
                else:
                    print("[migration] in_stock column already exists in PostgreSQL")
        except Exception as e:
            print(f"[migration] Error checking/adding in_stock column: {e}")
            import traceback
            traceback.print_exc()
            # Re-raise to prevent app from starting with broken schema
            raise
        return
    
    # SQLite: check and add column
    with db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(inventory)"))
        columns = [row[1] for row in result]
    if "in_stock" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE inventory ADD COLUMN in_stock BOOLEAN DEFAULT 1 NOT NULL"))
            conn.execute(text("UPDATE inventory SET in_stock = 1 WHERE in_stock IS NULL"))
        print("[migration] Added in_stock column to SQLite inventory table")
    else:
        print("[migration] in_stock column already exists in SQLite")


def ensure_inventory_status_columns():
    """Ensure Inventory table tracks status/deleted_at instead of hard deletes."""
    inspector = db.inspect(db.engine)
    columns = [col["name"] for col in inspector.get_columns("inventory")]
    is_pg = is_postgres()

    if "status" not in columns:
        print("[migration] Adding status column to inventory...")
        ddl = "ALTER TABLE inventory ADD COLUMN status VARCHAR(20) DEFAULT 'active' NOT NULL" if is_pg else "ALTER TABLE inventory ADD COLUMN status TEXT DEFAULT 'active' NOT NULL"
        with db.engine.begin() as conn:
            conn.execute(text(ddl))
            conn.execute(text("UPDATE inventory SET status = 'active' WHERE status IS NULL"))

    if "deleted_at" not in columns:
        print("[migration] Adding deleted_at column to inventory...")
        ddl = "ALTER TABLE inventory ADD COLUMN deleted_at TIMESTAMP NULL" if is_pg else "ALTER TABLE inventory ADD COLUMN deleted_at DATETIME NULL"
        with db.engine.begin() as conn:
            conn.execute(text(ddl))


def ensure_pg_search_indexes():
    """Ensure PostgreSQL search indexes/extensions exist for faster queries."""
    if not is_postgres():
        return
    try:
        with db.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_book_title_title_trgm ON book_title USING gin (title gin_trgm_ops)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_book_title_title_tsv ON book_title USING gin (to_tsvector('simple', title))"
                )
            )
    except Exception as err:
        print(f"[search] failed to ensure PostgreSQL indexes/extensions: {err}")


def migrate_legacy_books_into_inventory():
    """One-time migration: move rows from old book table into new normalized tables."""
    if is_postgres():
        return  # legacy sqlite-only migration
    with db.engine.connect() as conn:
        has_legacy = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='book'")
        ).fetchone()
    if not has_legacy:
        return

    # If inventory already has data, assume migration done
    if db.session.query(Inventory).first():
        return

    ensure_author_column()

    with db.engine.connect() as conn:
        legacy_rows = conn.execute(
            text("SELECT title, author, in_stock, cabinet_id FROM book")
        ).fetchall()

    if not legacy_rows:
        return

    # aggregate by (title, cabinet) so qty reflects copies
    aggregate = Counter()
    authors = {}
    for row in legacy_rows:
        title, author, in_stock, cabinet_id = row
        qty = 1 if in_stock else 0
        key = (title or "", cabinet_id)
        aggregate[key] += qty
        if title not in authors and author:
            authors[title] = author

    for (raw_title, cabinet_id), qty in aggregate.items():
        if not raw_title or not cabinet_id:
            continue
        if qty <= 0:
            continue
        cabinet = Cabinet.query.get(cabinet_id)
        if not cabinet:
            continue
        title_obj = BookTitle.query.filter_by(title=raw_title).first()
        if not title_obj:
            title_obj = BookTitle(title=raw_title, author=authors.get(raw_title, ""))
            db.session.add(title_obj)
            db.session.flush()

        # Defensive check: ensure title_obj and cabinet have valid ids
        if not title_obj.id or not cabinet.id:
            print(f"[migrate_legacy_books][error] Skipping: title_id={title_obj.id if title_obj else None}, cabinet_id={cabinet.id if cabinet else None}")
            continue

        inventory = Inventory(
            title_id=title_obj.id,
            cabinet_id=cabinet.id,
        )
        db.session.add(inventory)

    db.session.commit()


def drop_legacy_book_table():
    """Remove legacy book table after migration to avoid confusion."""
    if is_postgres():
        return
    with db.engine.connect() as conn:
        has_legacy = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='book'")
        ).fetchone()
    if not has_legacy:
        return
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS book"))
    print("[cleanup] Dropped legacy book table")


def get_or_create_title(title, author=None):
    """Fetch or create a BookTitle record."""
    clean_title = (title or "").strip()
    if not clean_title:
        return None

    existing = BookTitle.query.filter_by(title=clean_title).first()
    if existing:
        if author and not existing.author:
            existing.author = author
            db.session.flush()
        return existing

    new_title = BookTitle(title=clean_title, author=(author or "").strip() or None)
    db.session.add(new_title)
    db.session.flush()
    return new_title


COVER_PLACEHOLDER_URL = "https://placehold.co/240x320?text=No+Cover"
DEFAULT_ALLOWED_COVER_HOSTS = "cwgv.com.tw,*.cwgv.com.tw,placehold.co"


def _normalize_cover_host_pattern(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw or raw == "*":
        return ""
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        raw = parsed.netloc or parsed.path
    raw = raw.split("/", 1)[0].strip()
    if not raw or raw == "*":
        return ""
    if raw.startswith("*."):
        host = raw[2:]
        return f"*.{host.strip('.')}" if host.strip(".") else ""
    return raw.strip(".")


ALLOWED_COVER_HOSTS = tuple(
    dict.fromkeys(
        pattern
        for pattern in (
            _normalize_cover_host_pattern(host)
            for host in os.environ.get("ALLOWED_COVER_HOSTS", DEFAULT_ALLOWED_COVER_HOSTS).split(",")
        )
        if pattern
    )
)


def _cover_host_allowed(host: str | None) -> bool:
    clean_host = (host or "").strip().lower().strip(".")
    if not clean_host:
        return False
    for pattern in ALLOWED_COVER_HOSTS:
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if clean_host.endswith(f".{suffix}"):
                return True
        elif clean_host == pattern:
            return True
    return False


def cover_csp_sources() -> list[str]:
    """Return CSP image sources generated from the configured cover host allowlist."""
    sources = []
    for pattern in ALLOWED_COVER_HOSTS:
        if pattern.startswith("*."):
            sources.append(f"https://*.{pattern[2:]}")
        else:
            sources.append(f"https://{pattern}")
    return sources


def is_allowed_cover_url(url: str | None) -> bool:
    """Return True when a cover image URL can be served from an approved host."""
    text_value = (url or "").strip()
    if not text_value:
        return False
    if text_value.startswith("/") and not text_value.startswith("//"):
        return True
    try:
        parsed = urllib.parse.urlparse(text_value)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not parsed.scheme and parsed.netloc:
        return _cover_host_allowed(host)
    return parsed.scheme == "https" and _cover_host_allowed(host)


def normalize_cover_url(url: str | None) -> str:
    """Return a safe cover URL, or an empty string when the source is not allowed."""
    text_value = (url or "").strip()
    if not text_value:
        return ""
    if text_value.startswith("/") and not text_value.startswith("//"):
        return text_value
    try:
        parsed = urllib.parse.urlparse(text_value)
    except Exception:
        return ""
    host = (parsed.hostname or "").lower()
    if not parsed.scheme and parsed.netloc and _cover_host_allowed(host):
        return urllib.parse.urlunparse(parsed._replace(scheme="https"))
    if parsed.scheme == "http" and _cover_host_allowed(host):
        return urllib.parse.urlunparse(parsed._replace(scheme="https"))
    return text_value if parsed.scheme == "https" and _cover_host_allowed(host) else ""


def cover_url_for_title(title_obj):
    """Return stored cover link or placeholder."""
    if not title_obj:
        return COVER_PLACEHOLDER_URL
    cover_url = normalize_cover_url(getattr(title_obj, "cover_link", ""))
    if cover_url:
        return cover_url
    return COVER_PLACEHOLDER_URL


def _normalized_identifier(username: str, email: str) -> str:
    return f"{(username or '').strip().lower()}|{(email or '').strip().lower()}"


def generate_invite_code(length: int = 10) -> str:
    """Generate a random alphanumeric invite code."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_invite_code(code: str) -> str:
    """Normalize invite codes so copy/paste casing and spacing do not matter."""
    return re.sub(r"\s+", "", code or "").upper()


def invite_code_pepper() -> str:
    """Return the stable secret used for invite lookup HMACs."""
    pepper = os.environ.get("INVITE_CODE_PEPPER")
    if pepper:
        return pepper
    if STRICT_HOSTED_PRODUCTION:
        raise RuntimeError("INVITE_CODE_PEPPER is required in hosted production")
    return app.secret_key


def invite_code_lookup(code: str) -> str:
    """Return a deterministic, non-reversible lookup key for an invite code."""
    normalized = normalize_invite_code(code)
    secret = invite_code_pepper().encode("utf-8")
    return hmac.new(secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_invite_code(code: str) -> str:
    """Return a salted hash for final invite-code verification."""
    return generate_password_hash(normalize_invite_code(code))


def verify_invite_code(invite: AdminInvite, code: str) -> bool:
    """Verify a submitted code against a hashed invite row."""
    normalized = normalize_invite_code(code)
    if not invite or not normalized:
        return False
    if invite.code_hash:
        return check_password_hash(invite.code_hash, normalized)
    return hmac.compare_digest(invite.code or "", normalized)


def invite_reference(invite_id: int | None = None) -> str:
    """Generate a non-secret placeholder for the legacy plaintext code column."""
    suffix = str(invite_id) if invite_id else secrets.token_hex(8)
    return f"issued-{suffix}-{secrets.token_hex(4)}"[:32]


def find_valid_invite(code: str) -> AdminInvite | None:
    """Find and verify an unused invite without storing plaintext codes."""
    normalized = normalize_invite_code(code)
    if not normalized:
        return None

    lookup = invite_code_lookup(normalized)
    invite = AdminInvite.query.filter_by(code_lookup=lookup, used_at=None).first()
    if invite and verify_invite_code(invite, normalized):
        return invite

    # Backward-compatible path for pre-migration plaintext invite rows.
    legacy_invite = AdminInvite.query.filter_by(code=normalized, used_at=None).first()
    if legacy_invite and verify_invite_code(legacy_invite, normalized):
        legacy_invite.code_hash = hash_invite_code(normalized)
        legacy_invite.code_lookup = lookup
        legacy_invite.code = invite_reference(legacy_invite.id)
        return legacy_invite

    # Resilient fallback for old deployments where the lookup pepper changed.
    hashed_invites = (
        AdminInvite.query
        .filter(AdminInvite.used_at.is_(None))
        .filter(AdminInvite.code_hash.isnot(None))
        .all()
    )
    for candidate in hashed_invites:
        if verify_invite_code(candidate, normalized):
            candidate.code_lookup = lookup
            if not candidate.code or not candidate.code.startswith("issued-"):
                candidate.code = invite_reference(candidate.id)
            return candidate
    return None


def is_postgres():
    return bool(DATABASE_URL) and DATABASE_URL.startswith("postgresql://")


print(f"[db] using {masked_db_uri(app.config['SQLALCHEMY_DATABASE_URI'])}")


def ensure_admin_email_column():
    """Ensure admin_user table has email column (SQLite-friendly)."""
    inspector = db.inspect(db.engine)
    columns = [col["name"] for col in inspector.get_columns("admin_user")]
    if "email" not in columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE admin_user ADD COLUMN email VARCHAR(255)"))
            conn.commit()
    if "role" not in columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE admin_user ADD COLUMN role VARCHAR(50) DEFAULT 'admin'"))
            conn.commit()
    if "admin_invite" not in inspector.get_table_names():
        AdminInvite.__table__.create(db.engine)
    else:
        invite_cols = [col["name"] for col in inspector.get_columns("admin_invite")]
        if "role" not in invite_cols:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE admin_invite ADD COLUMN role VARCHAR(50)"))
                conn.commit()
        if "code_hash" not in invite_cols:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE admin_invite ADD COLUMN code_hash VARCHAR(255)"))
                conn.commit()
        if "code_lookup" not in invite_cols:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE admin_invite ADD COLUMN code_lookup VARCHAR(64)"))
                conn.commit()
        try:
            with db.engine.connect() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_invite_code_lookup ON admin_invite (code_lookup)"))
                conn.commit()
        except Exception as exc:
            print(f"[migration] admin_invite code_lookup index skipped: {exc}")


def migrate_plaintext_invite_codes():
    """Convert legacy plaintext invite codes to lookup + salted hash rows."""
    invites = AdminInvite.query.all()
    changed = False
    for invite in invites:
        raw_code = (invite.code or "").strip()
        placeholder = raw_code.startswith("issued-")
        if raw_code and not placeholder and (not invite.code_hash or not invite.code_lookup):
            normalized = normalize_invite_code(raw_code)
            invite.code_hash = invite.code_hash or hash_invite_code(normalized)
            invite.code_lookup = invite.code_lookup or invite_code_lookup(normalized)
            invite.code = invite_reference(invite.id)
            changed = True
        elif raw_code and not placeholder and invite.code_hash and invite.code_lookup:
            invite.code = invite_reference(invite.id)
            changed = True
    if changed:
        db.session.commit()


def ensure_advance_admin_exists():
    """Keep one owner-level admin available before enforcing stricter RBAC."""
    if STRICT_HOSTED_PRODUCTION and not env_flag("EXIS_ENABLE_ADMIN_BOOTSTRAP", False):
        print("[init] advance-admin auto-promotion skipped in hosted production.")
        return
    if AdminUser.query.filter_by(role="advance-admin").first():
        return
    preferred = None
    if ADMIN_USERNAME:
        preferred = AdminUser.query.filter_by(username=ADMIN_USERNAME).first()
    preferred = preferred or AdminUser.query.order_by(AdminUser.id.asc()).first()
    if not preferred:
        return
    preferred.role = "advance-admin"
    db.session.commit()
    print(f"[init] promoted admin '{preferred.username}' to advance-admin")


def ensure_default_admin():
    """Create a default admin user when none exist, using env credentials."""
    if AdminUser.query.first():
        return
    if STRICT_HOSTED_PRODUCTION and not env_flag("EXIS_ENABLE_ADMIN_BOOTSTRAP", False):
        print("[init] default admin seeding skipped in hosted production. Use tools/create_admin_code.py for onboarding.")
        return
    password = ADMIN_PASSWORD_RAW
    password_hash = ADMIN_PASSWORD_HASH
    if not password and not password_hash:
        print("[init] No default admin seeded (no ADMIN_PASSWORD/ADMIN_PASSWORD_HASH set).")
        return

    username = ADMIN_USERNAME or "admin"
    if not password_hash:
        password_hash = generate_password_hash(password)

    email = DEFAULT_ADMIN_EMAIL
    user = AdminUser(
        username=username,
        email=email,
        password_hash=password_hash,
        role="admin",
    )
    db.session.add(user)
    db.session.commit()
    log_action("seed_admin", target=username, details="created default admin user")
    db.session.commit()


LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", "1"))


def active_books_query():
    """Return a query for active inventory rows."""
    return Book.query.filter(Book.status == "active")


def current_actor():
    return session.get("admin_user") or ADMIN_USERNAME or "admin"


def log_action(action, target=None, details=None):
    """Persist a simple audit record."""
    entry = AuditLog(
        actor=current_actor(),
        action=action,
        target=target,
        details=details,
    )
    db.session.add(entry)
    # Note: Don't commit here - let caller commit to maintain transaction integrity


def initialize_app(*, sync_csv: bool = True):
    """Run one-time startup tasks."""
    print("[init] starting app initialization")
    with app.app_context():
        db.create_all()
        print("[init] tables ensured")
        print("[init] checking schema...")
        ensure_admin_email_column()
        migrate_plaintext_invite_codes()
        ensure_default_admin()
        ensure_advance_admin_exists()
        ensure_title_cover_column()
        ensure_title_topics_column()
        ensure_book_title_view_columns()
        ensure_event_date_columns()
        ensure_cabinet_type_column()
        ensure_inventory_in_stock_column()
        ensure_inventory_status_columns()
        ensure_pg_search_indexes()
        print("[init] migrating schema if needed...")
        drop_quantity_columns_from_sqlite()  # Remove old quantity columns
        migrate_legacy_books_into_inventory()
        drop_legacy_book_table()
        if sync_csv:
            print("[init] syncing CSV if needed...")
            sync_csv_to_db()
        else:
            print("[init] CSV sync skipped by command option")
    print("[init] done")


def should_run_startup_init() -> bool:
    """Return whether schema/data maintenance may run during web startup."""
    if env_flag_any(("EXIS_SKIP_STARTUP_INIT", "SKIP_INIT"), False):
        return False
    return env_flag("EXIS_AUTO_INIT", not IS_HOSTED_DEPLOY)


if should_run_startup_init():
    initialize_app()
else:
    print("[init] startup schema/data maintenance skipped")
    print("[init] run `python -m database.tools.db_tools init-db` before deployment when schema changed")

def cabinet_type_name(cabinet):
    """Return the normalized cabinet type string."""
    if not cabinet or not getattr(cabinet, "type", None):
        return ""
    return cabinet.type.strip().lower()

def cabinet_to_dict(cabinet):
    """Serialize a cabinet record for JSON responses."""
    cab_type = cabinet_type_name(cabinet) or "display"
    active_books = [b for b in getattr(cabinet, "books", []) if getattr(b, "status", "active") == "active"]
    return {
        "id": cabinet.id,
        "name": cabinet.name,
        "type": cab_type,
        "book_count": len(active_books),
    }


def book_to_dict(book):
    """Serialize a book record for JSON responses."""
    return {
        "id": book.id,
        "title": book.title,
        "cover_url": cover_url_for_title(getattr(book, "book_title", None)),
        "in_stock": book.in_stock,
        "cabinet_id": book.cabinet_id,
        "cabinet_name": book.cabinet.name if book.cabinet else "",
        "author": book.author,
    }


RESERVE_SUFFIX_PATTERN = re.compile(r"書櫃下")
EMPTY_PARENS_PATTERNS = (
    re.compile(r"\(\s*\)"),
    re.compile(r"（\s*）"),
)


def strip_reserve_hint(name: str) -> str:
    """Remove reserve suffix hints from cabinet labels when displaying."""
    if not name:
        return ""
    sanitized = RESERVE_SUFFIX_PATTERN.sub("", name)
    for pattern in EMPTY_PARENS_PATTERNS:
        sanitized = pattern.sub("", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized)
    return sanitized.strip()


def purge_empty_reserve_books():
    """Remove reserve cabinet entries with no stock.
    
    Note: Since quantity tracking is removed, this function is now a no-op.
    All inventory records represent books that are in stock.
    """
    return 0


def build_grouped_book_entries(
    books,
    *,
    include_id=False,
    include_cabinet_id=False,
    reference_books=None,
    include_reserve=True,
    include_reserve_out_of_stock=False,
    sort_by_stock=False,
    show_counts=False,
):
    """Group books by title and derive display metadata."""
    if not books:
        return {}

    reference_books = reference_books or books

    reference_by_title = defaultdict(list)
    for ref_book in reference_books:
        if getattr(ref_book, "status", "active") != "active":
            continue
        reference_by_title[ref_book.title].append(ref_book)

    books_by_title = defaultdict(list)
    for book in books:
        if getattr(book, "status", "active") != "active":
            continue
        books_by_title[book.title].append(book)

    grouped_entries = []
    for title, title_books in books_by_title.items():
        reference_list = reference_by_title.get(title, title_books)
        any_in_stock = any(ref.in_stock for ref in reference_list)
        all_in_stock = all(ref.in_stock for ref in reference_list)
        has_reserve_stock = any(
            ref.cabinet
            and (ref.cabinet.type or "").strip().lower() == "reserve"
            and ref.in_stock
            for ref in reference_list
        )
        has_display_stock = any(
            ref.cabinet
            and (ref.cabinet.type or "").strip().lower() == "display"
            and ref.in_stock
            for ref in reference_list
        )
        reserve_sources = sorted(
            {
                ref.cabinet.name
                for ref in reference_list
                if ref.cabinet
                and (ref.cabinet.type or "").strip().lower() == "reserve"
                and ref.in_stock
            }
        )
        reserve_sources = sorted(
            {strip_reserve_hint(name) for name in reserve_sources if name}
        )
        has_display = any(
            ref.cabinet
            and (ref.cabinet.type or "").strip().lower() == "display"
            for ref in reference_list
        )
        has_display_out = any(
            not ref.in_stock
            and ref.cabinet
            and (ref.cabinet.type or "").strip().lower() == "display"
            for ref in reference_list
        )
        reserve_in_stock = any(
            ref.cabinet
            and (ref.cabinet.type or "").strip().lower() == "reserve"
            and ref.in_stock
            for ref in reference_list
        )

        note_text = None
        if reserve_sources and has_display_out:
            note_text = "📦 請取備書" if include_reserve else "請通知工作人員補書"
        formatted_entries = []
        note_targets = []
        display_in_subset = any(
            b.cabinet
            and (b.cabinet.type or "").strip().lower() == "display"
            for b in title_books
        )
        for book in title_books:
            cabinet = book.cabinet
            cabinet_type = (cabinet.type or "").strip().lower() if cabinet else ""
            raw_cabinet_name = cabinet.name if cabinet else "未知櫃位"
            cabinet_name = strip_reserve_hint(raw_cabinet_name)

            if cabinet and cabinet_type == "reserve":
                if not include_reserve:
                    continue
                if not book.in_stock and not include_reserve_out_of_stock:
                    continue
                # Keep reserve rows visible even when replenish notes are shown.

            in_stock = book.in_stock
            if cabinet_type == "display":
                if in_stock:
                    status = "展示中"
                elif has_reserve_stock:
                    status = "暫無展示"
                else:
                    status = "缺貨"
            else:
                status = "備書可取" if in_stock else "備書缺貨"

            entry = {
                "cabinet": cabinet_name,
                "status": status,
                "cls": "in-stock" if in_stock else "out-stock",
                "notes": [],
            }
            if include_id:
                entry["id"] = book.id
            if include_cabinet_id:
                entry["cabinet_id"] = book.cabinet_id
            
            # Add replenish info for display cabinets that are out of stock but have reserve stock
            if cabinet_type == "display" and not in_stock and has_reserve_stock:
                # Find the first available reserve book for this title
                reserve_book = next(
                    (ref for ref in reference_list 
                    if ref.cabinet 
                    and (ref.cabinet.type or "").strip().lower() == "reserve"
                    and ref.in_stock),
                    None
                )
                if reserve_book:
                    entry["replenish"] = {
                        "reserve_book_id": reserve_book.id,
                        "reserve_cabinet_id": reserve_book.cabinet_id,
                        "reserve_cabinet_name": reserve_book.cabinet.name if reserve_book.cabinet else "",
                        "display_cabinet_id": book.cabinet_id,
                    }

            formatted_entries.append(entry)

            if (
                note_text
                and not in_stock
                and cabinet
                and cabinet_type == "display"
            ):
                note_targets.append(len(formatted_entries) - 1)

        if note_text and note_targets:
            formatted_entries[note_targets[-1]]["notes"].append(note_text)

        rank = 0 if not any_in_stock else (1 if not all_in_stock else 2)
        grouped_entries.append((title, formatted_entries, rank))

    if sort_by_stock:
        grouped_entries.sort(key=lambda item: (item[2], item[0]))

    return {title: entries for title, entries, _ in grouped_entries}


def get_csrf_token():
    """Return a per-session CSRF token, creating one when needed."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

def collect_replenish_alerts():
    """Dynamically scan for books/cabinets that need attention (optimized)."""
    alerts = []

    # 1) Display out-of-stock but reserve in-stock (needs replenishment).
    display_out_titles_sub = (
        db.session.query(Inventory.title_id)
        .join(Cabinet)
        .filter(Inventory.status == "active")
        .filter(Inventory.in_stock.is_(False))
        .filter(func.lower(Cabinet.type) == "display")
        .subquery()
    )

    reserve_in_titles_sub = (
        db.session.query(Inventory.title_id)
        .join(Cabinet)
        .filter(Inventory.status == "active")
        .filter(Inventory.in_stock.is_(True))
        .filter(func.lower(Cabinet.type) == "reserve")
        .subquery()
    )

    replenish_results = (
        db.session.query(BookTitle.title)
        .filter(BookTitle.id.in_(display_out_titles_sub))
        .filter(BookTitle.id.in_(reserve_in_titles_sub))
        .distinct()
        .limit(20)
        .all()
    )

    for row in replenish_results:
        alerts.append({
            "type": "low-stock",
            "message": f"《{row.title}》展示缺貨，可從備書補貨",
        })

    # 1b) Display out-of-stock with no reserve stock (fully empty).
    fully_out_results = (
        db.session.query(BookTitle.title)
        .filter(BookTitle.id.in_(display_out_titles_sub))
        .filter(~BookTitle.id.in_(reserve_in_titles_sub))
        .distinct()
        .limit(20)
        .all()
    )

    for row in fully_out_results:
        alerts.append({
            "type": "out-of-stock",
            "message": f"❌《{row.title}》展示缺貨且備書也無庫存",
        })

    # 2) Titles that exist in reserve cabinets but not in display cabinets.
    display_titles_sub = (
        db.session.query(Inventory.title_id)
        .join(Cabinet)
        .filter(Inventory.status == "active")
        .filter(func.lower(Cabinet.type) == "display")
        .subquery()
    )

    reserve_only_results = (
        db.session.query(BookTitle.title)
        .join(Inventory)
        .join(Cabinet)
        .filter(Inventory.status == "active")
        .filter(func.lower(Cabinet.type) == "reserve")
        .filter(~BookTitle.id.in_(display_titles_sub))
        .distinct()
        .limit(10)
        .all()
    )

    for row in reserve_only_results:
        alerts.append({
            "type": "info",
            "message": f"➡️《{row.title}》僅存在備書櫃，未展示"
        })

    # 3) Empty cabinets (no active inventory).
    empty_cabs = (
        db.session.query(Cabinet.name)
        .outerjoin(
            Inventory,
            (Cabinet.id == Inventory.cabinet_id) & (Inventory.status == "active"),
        )
        .filter(Inventory.id.is_(None))
        .all()
    )

    for row in empty_cabs:
        alerts.append({
            "type": "info",
            "message": f"➡️櫃位「{row.name}」目前沒有書籍"
        })

    return alerts

@app.before_request
def ensure_schema_migrations():
    """Ensure critical schema migrations are applied before handling requests."""
    request_check_default = should_run_startup_init() and not IS_HOSTED_DEPLOY
    if not env_flag("EXIS_REQUEST_SCHEMA_CHECK", request_check_default):
        return
    # Only check once per app instance to avoid performance issues
    if not hasattr(app, '_schema_migrations_checked'):
        try:
            with app.app_context():
                ensure_title_cover_column()
                ensure_title_topics_column()
                ensure_inventory_in_stock_column()
                ensure_inventory_status_columns()
                ensure_pg_search_indexes()
                ensure_event_date_columns()
                ensure_admin_email_column()
                migrate_plaintext_invite_codes()
                ensure_advance_admin_exists()
                inspector = db.inspect(db.engine)
                if "top_seller_snapshot" not in inspector.get_table_names():
                    TopSellerSnapshot.__table__.create(db.engine)
                if "event_schedule" not in inspector.get_table_names():
                    EventSchedule.__table__.create(db.engine)
                if "event_books" not in inspector.get_table_names():
                    event_books.create(db.engine)
                if "backup_archive" not in inspector.get_table_names():
                    BackupArchive.__table__.create(db.engine)
            app._schema_migrations_checked = True
        except Exception as e:
            print(f"[warning] Schema migration check failed: {e}")
            # Don't block requests, but log the error


@app.before_request
def enforce_admin_session_freshness():
    """Invalidate admin sessions when the backing account was removed or changed."""
    if request.endpoint and request.endpoint.startswith("static"):
        return
    if not session.get("is_admin"):
        return

    user = None
    admin_id = session.get("admin_id")
    try:
        if admin_id:
            user = AdminUser.query.get(admin_id)
        if not user and session.get("admin_user"):
            user = AdminUser.query.filter_by(username=session.get("admin_user")).first()
    except Exception:
        current_path = request.path or ""
        admin_paths = (
            "/admin",
            "/api/",
            "/cabinets",
            "/modify_cabinet",
            "/add_book",
            "/replenish",
            "/toggle",
            "/book_card",
        )
        if current_path.startswith(admin_paths):
            session.clear()
        return

    if not user:
        session.clear()
        return

    session["admin_id"] = user.id
    session["admin_user"] = user.username
    session["admin_role"] = user.role or "admin"


@app.before_request
def csrf_protect():
    """Lightweight CSRF protection for all state-changing requests."""
    if request.endpoint and request.endpoint.startswith("static"):
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        # Ensure a token exists for subsequent POSTs
        get_csrf_token()
        return

    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    session_token = session.get("csrf_token")
    if not token or not session_token or not hmac.compare_digest(str(token), str(session_token)):
        # For JSON requests, return JSON error
        if request.is_json or request.path.startswith('/api/') or request.path.startswith('/toggle_modal_stock') or request.path.startswith('/replenish'):
            if os.environ.get("CSRF_DEBUG") == "1":
                return jsonify({
                    "success": False,
                    "message": "Invalid or missing CSRF token.",
                    "debug": {
                        "token_present": bool(token),
                        "session_token_present": bool(session_token),
                        "token_prefix": (token or "")[:8],
                        "session_token_prefix": (session_token or "")[:8],
                    },
                }), 400
            return jsonify({"success": False, "message": "Invalid or missing CSRF token."}), 400
        abort(400, description="Invalid or missing CSRF token.")


@app.errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(error):
    """Return a controlled response when uploads exceed MAX_CONTENT_LENGTH."""
    message = "上傳檔案過大，請縮小後再試。"
    if request.is_json or request.path.startswith("/api/"):
        return jsonify({"success": False, "message": message}), 413
    return message, 413


def build_content_security_policy() -> str:
    img_sources = " ".join(dict.fromkeys(["'self'", "data:", "blob:", *cover_csp_sources()]))
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "script-src-elem 'self'",
        "script-src-attr 'none'",
        "style-src 'self'",
        "style-src-elem 'self'",
        "style-src-attr 'none'",
        f"img-src {img_sources}",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-src 'none'",
        "worker-src 'self'",
        "manifest-src 'self'",
        "media-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    if IS_HOSTED_DEPLOY:
        directives.append("upgrade-insecure-requests")
    return "; ".join(directives)


@app.after_request
def set_response_headers(response):
    """Apply security headers and keep HTML uncached for fresh deploys."""
    response.headers.setdefault("Content-Security-Policy", build_content_security_policy())
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    if IS_HOSTED_DEPLOY:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    if request.endpoint and request.endpoint.startswith("static"):
        return response
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token()}

@app.context_processor
def inject_is_admin():
    """Expose admin flag to templates for conditional UI."""
    role = session.get("admin_role") or ""
    if session.get("is_admin") and session.get("admin_id"):
        try:
            user = AdminUser.query.get(session.get("admin_id"))
            if user:
                role = user.role or "admin"
                session["admin_role"] = role
        except Exception:
            role = role or "admin"
    return {
        "is_admin": bool(session.get("is_admin")),
        "admin_role": role,
        "can_view_sensitive_admin": role == "advance-admin",
    }

@app.context_processor
def inject_static_version():
    """Expose a safe static asset version string for cache busting."""
    return {"static_version": app.config.get("STATIC_VERSION", "")}


@app.context_processor
def inject_app_version():
    """Expose the released application version to templates."""
    return {"app_version": app.config["APP_VERSION"]}


@app.context_processor
def inject_cover_policy():
    """Expose the same cover host allowlist used by backend validation."""
    return {"allowed_cover_hosts": list(ALLOWED_COVER_HOSTS)}


@app.context_processor
def inject_public_event_state():
    """Expose whether the public event anchor should be available."""
    if request.blueprint == "admin":
        return {"public_events_available": False}
    try:
        has_events = (
            db.session.query(EventSchedule.id)
            .filter(EventSchedule.is_active.is_(True))
            .first()
            is not None
        )
    except Exception:
        has_events = False
    return {"public_events_available": has_events}

def register_blueprints(app):
    from routes.auth import auth_bp
    from routes.inventory import inventory_bp
    from routes.api import api_bp
    from routes.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)


register_blueprints(app)
