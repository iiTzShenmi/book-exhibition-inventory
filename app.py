import csv
import os
import secrets
import re
import shutil
import json
import io
import urllib.parse
import urllib.request
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from sqlalchemy import text, func
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask, abort, jsonify, request, session
import redis
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from tools import env_loader  # loads .env into os.environ
from database.models import db, Book, Cabinet, BookTitle, Inventory, AuditLog, AdminUser, AdminInvite
from database.models import ViewEvent, TopSellerSnapshot, EventSchedule, BackupArchive
from similarity import BookProfile, suggest_for_missing_title, parse_topics_field

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
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
IS_PRODUCTION = os.environ.get("FLASK_ENV") == "production" or bool(DATABASE_URL)

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
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("APP_SECRET_KEY")
    or secrets.token_hex(32)
)
# Session configuration for security
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['SESSION_COOKIE_SECURE'] = os.environ.get("FLASK_ENV") == "production"  # HTTPS only in production
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

REDIS_URL = os.environ.get("REDIS_URL")
redis_client = None
_local_cache: dict[str, tuple[datetime, object]] = {}

if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL)
        print(f"[cache] connected to redis at {REDIS_URL}")
    except Exception as err:
        print(f"[cache] redis connection failed ({err}); falling back to in-process cache")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=REDIS_URL if redis_client else "memory://",
    default_limits=[],
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


def sync_csv_to_db():
    """Import or update the database from CSV (one-way).

    CSV columns supported:
    - cabinet_name, title, qty_or_bool, author (optional)
    """
    # Skip when running against remote DB unless explicitly enabled
    # Check ENABLE_CSV_SYNC - treat "1", "true", "yes" as enabled
    enable_sync = os.environ.get("ENABLE_CSV_SYNC", "").strip().lower()
    if is_postgres() and enable_sync not in ("1", "true", "yes"):
        print("[sync_csv_to_db] skipped (remote DB detected; set ENABLE_CSV_SYNC=1 to allow)")
        return
    
    # Skip if SKIP_INIT is set and database already has data
    skip_init = os.environ.get("SKIP_INIT", "").strip().lower()
    if skip_init and skip_init not in ("0", "false", "no"):
        # Check if database already has data
        if Inventory.query.first() is not None:
            print("[sync_csv_to_db] skipped (SKIP_INIT set and DB has data)")
            return
    
    if not os.path.exists(CSV_PATH):
        print(f"[sync_csv_to_db] CSV not found: {CSV_PATH}")
        return
    
    # Ensure quantity columns are removed before syncing (migration)
    # This is needed even when SKIP_INIT is set
    drop_quantity_columns_from_sqlite()

    def normalize_title(raw):
        # Simple normalization: strip and collapse internal whitespace
        return re.sub(r"\s+", " ", (raw or "").strip())

    aggregates = Counter()  # (cabinet_name, title) -> qty
    authors = {}
    csv_titles = set()

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                continue
            cab_name, title, qty_str, *rest = row
            csv_titles.add(normalize_title(title))
            author = (rest[0].strip() if rest else "") or None
            qty = parse_qty(qty_str)
            key = (cab_name.strip(), title.strip())
            aggregates[key] += qty
            if author and title not in authors:
                authors[title] = author

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
        # No quantity to update - inventory exists or doesn't

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

    db.session.commit()
    print("[sync_csv_to_db] CSV -> DB sync complete.")

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
                writer.writerow([
                    cab.name,
                    inv.title,
                    "True" if inv.in_stock else "False",
                    inv.author or "",
                ])
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
            result = subprocess.run(
                ["pg_dump", DATABASE_URL],
                check=True,
                capture_output=True,
            )
            with open(dump_path, "wb") as f:
                f.write(result.stdout)
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
    
    # Get all data (only columns we want to keep)
    try:
        with db.engine.connect() as read_conn:
            data = read_conn.execute(text("SELECT id, title_id, cabinet_id, created_at, updated_at FROM inventory")).fetchall()
    except Exception as e:
        print(f"[migration] Error reading inventory data: {e}")
        return
    
    # Drop old table and recreate - use begin() for transaction
    with db.engine.begin() as trans_conn:
        trans_conn.execute(text("DROP TABLE inventory"))
    
    # Recreate using model definition
    Inventory.__table__.create(db.engine, checkfirst=True)
    
    # Reinsert data
    if data:
        insert_sql = text("""
            INSERT INTO inventory (id, title_id, cabinet_id, created_at, updated_at)
            VALUES (:id, :title_id, :cabinet_id, :created_at, :updated_at)
        """)
        with db.engine.begin() as trans_conn:
            for row in data:
                try:
                    trans_conn.execute(insert_sql, {
                        "id": row[0],
                        "title_id": row[1],
                        "cabinet_id": row[2],
                        "created_at": row[3],
                        "updated_at": row[4]
                    })
                except Exception as e:
                    print(f"[migration] Warning: Failed to reinsert row {row[0]}: {e}")
    
    # Reset sequence if needed
    try:
        with db.engine.begin() as seq_conn:
            max_id = seq_conn.execute(text("SELECT MAX(id) FROM inventory")).scalar() or 0
            if max_id:
                seq_conn.execute(text(f"UPDATE sqlite_sequence SET seq = {max_id} WHERE name = 'inventory'"))
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
    if is_postgres():
        return  # column exists in model; PRAGMA not supported
    with db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(book_title)"))
        columns = [row[1] for row in result]
    if "cover_link" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE book_title ADD COLUMN cover_link TEXT"))


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


def cover_url_for_title(title_obj):
    """Return stored cover link or placeholder."""
    if not title_obj:
        return COVER_PLACEHOLDER_URL
    if title_obj.cover_link:
        return title_obj.cover_link
    return COVER_PLACEHOLDER_URL


def _normalized_identifier(username: str, email: str) -> str:
    return f"{(username or '').strip().lower()}|{(email or '').strip().lower()}"


def generate_invite_code(length: int = 10) -> str:
    """Generate a random alphanumeric invite code."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))

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
    if "admin_invite" not in inspector.get_table_names():
        AdminInvite.__table__.create(db.engine)
    if "view_event" not in inspector.get_table_names():
        ViewEvent.__table__.create(db.engine)
    if "top_seller_snapshot" not in inspector.get_table_names():
        TopSellerSnapshot.__table__.create(db.engine)


CACHE_DURATION = int(os.environ.get("TOP_SELLER_CACHE_SECONDS", "300"))
TOP_SELLER_SNAPSHOT_MAX_AGE = int(os.environ.get("TOP_SELLER_SNAPSHOT_MAX_AGE", "3600"))


def _cache_get_json(key: str, max_age: int | None = None):
    """Fetch cached JSON from Redis or local fallback."""
    if redis_client:
        try:
            raw = redis_client.get(key)
            if raw:
                return json.loads(raw)
        except Exception as err:
            print(f"[cache] redis read failed for {key}: {err}")
    if key in _local_cache:
        ts, value = _local_cache[key]
        if max_age is None or (datetime.utcnow() - ts).total_seconds() < max_age:
            return value
        _local_cache.pop(key, None)
    return None


def _cache_set_json(key: str, value, ttl: int = CACHE_DURATION):
    """Cache JSON to Redis or local fallback."""
    if redis_client:
        try:
            redis_client.setex(key, ttl, json.dumps(value))
            return
        except Exception as err:
            print(f"[cache] redis write failed for {key}: {err}")
    _local_cache[key] = (datetime.utcnow(), value)


def refresh_top_sellers(limit: int = 8):
    """Compute top sellers and persist snapshot for reuse."""
    sellers: list[dict] = []
    now = datetime.utcnow()

    rows = (
        db.session.query(ViewEvent.title, func.count(ViewEvent.id).label("cnt"))
        .filter(ViewEvent.title != None)  # noqa: E711
        .group_by(ViewEvent.title)
        .order_by(func.count(ViewEvent.id).desc())
        .limit(limit * 2)
        .all()
    )

    if rows:
        top_titles = [r.title for r in rows]
        title_map = {
            bt.title: bt
            for bt in BookTitle.query.filter(BookTitle.title.in_(top_titles)).all()
        }
        for title, cnt in rows[:limit]:
            bt = title_map.get(title)
            sellers.append(
                {
                    "title": title,
                    "cover": cover_url_for_title(bt),
                    "count": cnt,
                }
            )

    if not sellers:
        top_titles = (
            BookTitle.query.join(Inventory)
            .filter(Inventory.status == "active")
            .order_by(Inventory.updated_at.desc())
            .limit(limit)
            .all()
        )
        for bt in top_titles:
            sellers.append({
                "title": bt.title,
                "cover": cover_url_for_title(bt),
                "count": None,
            })

    try:
        db.session.query(TopSellerSnapshot).filter_by(limit=limit).delete()
        db.session.add(
            TopSellerSnapshot(
                limit=limit,
                payload=json.dumps(sellers),
                calculated_at=now,
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[top_sellers] snapshot write failed: {exc}")

    _cache_set_json(f"top_sellers:{limit}", sellers, ttl=CACHE_DURATION)
    return sellers


def get_top_sellers(limit=8):
    """Return cached top sellers shared across workers."""
    cache_key = f"top_sellers:{limit}"
    cached = _cache_get_json(cache_key, max_age=CACHE_DURATION)
    if cached:
        return cached[:limit]

    snapshot = (
        TopSellerSnapshot.query.filter_by(limit=limit)
        .order_by(TopSellerSnapshot.calculated_at.desc())
        .first()
    )

    sellers: list[dict] = []
    now = datetime.utcnow()
    if snapshot and snapshot.payload:
        try:
            if not snapshot.calculated_at or (now - snapshot.calculated_at).total_seconds() < TOP_SELLER_SNAPSHOT_MAX_AGE:
                sellers = json.loads(snapshot.payload)
        except Exception:
            sellers = []

    if not sellers:
        sellers = refresh_top_sellers(limit=limit)

    _cache_set_json(cache_key, sellers, ttl=CACHE_DURATION)
    return sellers[:limit]


def ensure_default_admin():
    """Create a default admin user when none exist, using env credentials."""
    if AdminUser.query.first():
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
    log_action("seed_admin", target=username, details=f"created default admin user (email={email})")
    db.session.commit()


def log_view_event(title, source=None, actor=None):
    """Persist a view event for top-seller aggregation."""
    clean_title = (title or "").strip()
    if not clean_title:
        return
    evt = ViewEvent(
        title=clean_title,
        source=(source or "").strip() or None,
        actor=(actor or "").strip() or None,
    )
    db.session.add(evt)
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


def initialize_app():
    """Run one-time startup tasks."""
    print("[init] starting app initialization")
    with app.app_context():
        db.create_all()
        print("[init] tables ensured")
        print("[init] checking schema...")
        ensure_admin_email_column()
        ensure_default_admin()
        ensure_title_cover_column()
        ensure_cabinet_type_column()
        ensure_inventory_in_stock_column()
        ensure_inventory_status_columns()
        ensure_pg_search_indexes()
        print("[init] migrating schema if needed...")
        drop_quantity_columns_from_sqlite()  # Remove old quantity columns
        migrate_legacy_books_into_inventory()
        drop_legacy_book_table()
        print("[init] syncing CSV if needed...")
        sync_csv_to_db()
    print("[init] done")
# Check SKIP_INIT - treat "0", "", None, or unset as False (don't skip)
skip_init = os.environ.get("SKIP_INIT", "").strip().lower()
if skip_init and skip_init not in ("0", "false", "no"):
    print("[init] SKIP_INIT set; initialization skipped")
    # Still run critical schema migrations even if SKIP_INIT is set
    with app.app_context():
        print("[init] running critical schema migrations...")
        ensure_inventory_in_stock_column()
        ensure_inventory_status_columns()
        ensure_pg_search_indexes()
else:
    initialize_app()
print("\n[init] create_all done\n")

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
            "message": f"《{row.title}》僅存在備書櫃，未展示"
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
            "message": f"櫃位「{row.name}」目前沒有書籍"
        })

    return alerts

@app.before_request
def ensure_schema_migrations():
    """Ensure critical schema migrations are applied before handling requests."""
    # Only check once per app instance to avoid performance issues
    if not hasattr(app, '_schema_migrations_checked'):
        try:
            with app.app_context():
                ensure_inventory_in_stock_column()
                ensure_inventory_status_columns()
                ensure_pg_search_indexes()
                inspector = db.inspect(db.engine)
                if "top_seller_snapshot" not in inspector.get_table_names():
                    TopSellerSnapshot.__table__.create(db.engine)
                if "event_schedule" not in inspector.get_table_names():
                    EventSchedule.__table__.create(db.engine)
                if "backup_archive" not in inspector.get_table_names():
                    BackupArchive.__table__.create(db.engine)
            app._schema_migrations_checked = True
        except Exception as e:
            print(f"[warning] Schema migration check failed: {e}")
            # Don't block requests, but log the error

@app.before_request
def csrf_protect():
    """Lightweight CSRF protection for all state-changing requests."""
    if request.endpoint and request.endpoint.startswith("static"):
        return
    if request.endpoint in {"auth.login", "auth.register"}:
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        # Ensure a token exists for subsequent POSTs
        get_csrf_token()
        return

    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    session_token = session.get("csrf_token")
    if not session_token and token:
        session["csrf_token"] = token
        session_token = token
    if not token or token != session_token:
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


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token()}

@app.context_processor
def inject_is_admin():
    """Expose admin flag to templates for conditional UI."""
    return {"is_admin": bool(session.get("is_admin"))}

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
