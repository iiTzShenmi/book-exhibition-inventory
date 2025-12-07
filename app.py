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
from datetime import datetime
from collections import defaultdict, Counter
from sqlalchemy import text, func
from werkzeug.security import check_password_hash, generate_password_hash
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
    url_for,
)
from database.models import db, Book, Cabinet, BookTitle, Inventory, AuditLog, AdminUser, AdminInvite
from database.models import ViewEvent
from recommender import BookProfile, suggest_for_missing_title, parse_topics_field

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "database")
os.makedirs(DATA_DIR, exist_ok=True)
CSV_PATH = os.path.join(DATA_DIR, "inventory.csv")
DB_PATH = os.path.join(DATA_DIR, "inventory.db")
AUTO_GIT_PUSH = 1
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
LAST_BACKUP_META = os.path.join(BACKUP_DIR, "last_auto_backup.json")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
_plain_password = os.environ.get("ADMIN_PASSWORD")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = generate_password_hash(_plain_password or "1234")
DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("APP_SECRET_KEY")
    or secrets.token_hex(32)
)
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
    if is_postgres() and not os.environ.get("ENABLE_CSV_SYNC"):
        print("[sync_csv_to_db] skipped (remote DB detected; set ENABLE_CSV_SYNC=1 to allow)")
        return
    if not os.path.exists(CSV_PATH):
        print(f"[sync_csv_to_db] CSV not found: {CSV_PATH}")
        return

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
        inventory = Inventory.query.filter_by(
            title_id=title_obj.id, cabinet_id=cabinet.id
        ).first()
        if not inventory:
            inventory = Inventory(
                title_id=title_obj.id,
                cabinet_id=cabinet.id,
                qty_on_hand=qty,
                qty_reserved=0,
            )
            db.session.add(inventory)
        else:
            inventory.qty_on_hand = qty

    # Remove inventory rows no longer present
    for item in Inventory.query.join(Cabinet).join(BookTitle).all():
        pair = (item.cabinet.name if item.cabinet else "", item.title)
        if pair not in seen_pairs:
            db.session.delete(item)

    db.session.commit()
    print("[sync_csv_to_db] CSV -> DB sync complete.")

    # Report DB titles not present in CSV (potential renames/duplicates)
    if csv_titles:
        missing_titles = []
        for title_obj in BookTitle.query.all():
            norm = normalize_title(title_obj.title)
            if norm and norm not in csv_titles:
                count = (
                    Inventory.query.filter_by(title_id=title_obj.id).count()
                    if hasattr(title_obj, "inventories")
                    else 0
                )
                missing_titles.append((title_obj.title, count))
        if missing_titles:
            log_path = os.path.join(DATA_DIR, "book_csv_missing.txt")
            with open(log_path, "w", encoding="utf-8") as f:
                for title, count in missing_titles:
                    f.write(f"{title}\tinventory_count={count}\n")
            print(f"[sync_csv_to_db] Titles present in DB but missing in CSV: {len(missing_titles)}")
            print(f"[sync_csv_to_db] See details in {log_path}")


def export_db_to_csv():
    """Export database back to CSV (one-way)."""
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for cab in Cabinet.query.all():
            for inv in cab.books:
                writer.writerow([
                    cab.name,
                    inv.title,
                    "True" if (inv.qty_on_hand or 0) > 0 else "False",
                    inv.author or "",
                ])
    print("[export_db_to_csv] DB -> CSV export complete.")


def create_backup():
    """Create timestamped backups (Postgres: pg_dump; SQLite: file copy) plus CSV."""
    export_db_to_csv()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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

        inventory = Inventory(
            title_id=title_obj.id,
            cabinet_id=cabinet.id,
            qty_on_hand=max(qty, 0),
            qty_reserved=0,
        )
        db.session.add(inventory)

    db.session.commit()


def drop_legacy_book_table():
    """Remove legacy book table after migration to avoid confusion."""
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


def get_top_sellers(limit=8):
    """Compute top titles from DB view events; fallback to recent updates."""
    sellers = []
    cutoff = None

    counts_query = (
        db.session.query(ViewEvent.title, func.count(ViewEvent.id).label("cnt"))
        .filter(ViewEvent.title != None)  # noqa: E711
    )
    if cutoff:
        counts_query = counts_query.filter(ViewEvent.created_at >= cutoff)
    rows = (
        counts_query.group_by(ViewEvent.title)
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
    return sellers[:limit]


def ensure_default_admin():
    """Create a default admin user when none exist, using env credentials."""
    if AdminUser.query.first():
        return
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "1234")
    email = os.environ.get("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL)
    user = AdminUser(
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
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


def initialize_app():
    """Run one-time startup tasks."""
    with app.app_context():
        db.create_all()
        ensure_admin_email_column()
        ensure_default_admin()
        ensure_title_cover_column()
        ensure_cabinet_type_column()
        migrate_legacy_books_into_inventory()
        drop_legacy_book_table()
        sync_csv_to_db()
initialize_app()

def cabinet_type_name(cabinet):
    """Return the normalized cabinet type string."""
    if not cabinet or not getattr(cabinet, "type", None):
        return ""
    return cabinet.type.strip().lower()

def cabinet_to_dict(cabinet):
    """Serialize a cabinet record for JSON responses."""
    cab_type = cabinet_type_name(cabinet) or "display"
    return {
        "id": cabinet.id,
        "name": cabinet.name,
        "type": cab_type,
        "book_count": sum((b.qty_on_hand or 0) for b in cabinet.books),
    }


def book_to_dict(book):
    """Serialize a book record for JSON responses."""
    return {
        "id": book.id,
        "title": book.title,
        "cover_url": cover_url_for_title(getattr(book, "book_title", None)),
        "in_stock": book.in_stock,
        "qty_on_hand": book.qty_on_hand,
        "qty_reserved": book.qty_reserved,
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
    """Remove reserve cabinet entries with no stock."""
    stale_books = (
        Book.query.join(Cabinet)
        .filter(Book.qty_on_hand <= 0)
        .all()
    )
    removed = 0
    for book in stale_books:
        if cabinet_type_name(book.cabinet) == "reserve":
            db.session.delete(book)
            removed += 1
    if removed:
        db.session.commit()
        export_db_to_csv()
    return removed


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
        reference_by_title[ref_book.title].append(ref_book)

    books_by_title = defaultdict(list)
    for book in books:
        books_by_title[book.title].append(book)

    grouped_entries = []
    for title, title_books in books_by_title.items():
        reference_list = reference_by_title.get(title, title_books)
        any_in_stock = any((ref.qty_on_hand or 0) > 0 for ref in reference_list)
        all_in_stock = all((ref.qty_on_hand or 0) > 0 for ref in reference_list)
        has_reserve_stock = any(
            ref.cabinet
            and (ref.cabinet.type or "").strip().lower() == "reserve"
            and (ref.qty_on_hand or 0) > 0
            for ref in reference_list
        )
        has_display_stock = any(
            ref.cabinet
            and (ref.cabinet.type or "").strip().lower() == "display"
            and (ref.qty_on_hand or 0) > 0
            for ref in reference_list
        )
        reserve_sources = sorted(
            {
                ref.cabinet.name
                for ref in reference_list
                if ref.cabinet
                and (ref.cabinet.type or "").strip().lower() == "reserve"
                and (ref.qty_on_hand or 0) > 0
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
            and (ref.qty_on_hand or 0) > 0
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
                if book.in_stock and note_text and has_display and display_in_subset:
                    continue

            qty = book.qty_on_hand or 0
            in_stock = qty > 0
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
    """Dynamically scan for books/cabinets that need attention."""
    alerts = []

    # 1️⃣ Books out of stock but have reserve copies
    out_books = Book.query.filter(Book.qty_on_hand <= 0).all()
    seen_out = set()
    for book in out_books:
        if book.title in seen_out:
            continue
        seen_out.add(book.title)
        reserve_copy = (
            Book.query.filter_by(title_id=book.title_id)
            .join(Cabinet)
            .filter(Cabinet.type == "reserve", Book.qty_on_hand > 0)
            .first()
        )
        if reserve_copy:
            alerts.append({
                "type": "low-stock",
                "message": f"《{book.title}》已售完，請自「{reserve_copy.cabinet.name}」補貨"
            })
        else:
            alerts.append({
                "type": "out-of-stock",
                "message": f"《{book.title}》完全缺貨，無可補來源"
            })

    # 2️⃣ Books only exist in reserve cabinets
    reserve_books = Book.query.join(Cabinet).filter(Cabinet.type == "reserve", Book.qty_on_hand > 0).all()
    seen_reserve = set()
    for book in reserve_books:
        if book.title in seen_reserve:
            continue
        seen_reserve.add(book.title)
        display_copy = (
            Book.query.filter_by(title_id=book.title_id)
            .join(Cabinet)
            .filter(Cabinet.type == "display", Book.qty_on_hand > 0)
            .first()
        )
        if not display_copy:
            alerts.append({
                "type": "low-stock",
                "message": f"《{book.title}》僅存在備書櫃，未展示"
            })

    # 3️⃣ Empty cabinets (no books)
    empty_cabs = []
    for cab in Cabinet.query.all():
        has_books = (
            Book.query.filter_by(cabinet_id=cab.id)
            .filter(Book.qty_on_hand > 0)
            .first()
        )
        if not has_books:
            empty_cabs.append(cab.name)

    for cab_name in empty_cabs:
        alerts.append({
            "type": "info",
            "message": f"櫃位「{cab_name}」目前沒有書籍"
        })

    return alerts

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
    if not token or token != session.get("csrf_token"):
        abort(400, description="Invalid or missing CSRF token.")


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token()}

@app.context_processor
def inject_is_admin():
    """Expose admin flag to templates for conditional UI."""
    return {"is_admin": bool(session.get("is_admin"))}


# 🏠 Homepage (public)
@app.route("/")
def home():
    top_sellers = get_top_sellers(limit=8)
    return render_template(
        "home.html",
        title="書展庫存系統",
        show_top_sellers=True,
        top_sellers=top_sellers,
    )

@app.route("/admin")
def admin_dashboard():
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    query = request.args.get("filter", "").strip()
    cabinet_filter = request.args.get("cabinet", "")
    status_filter = request.args.get("status", "")
    author_filter = request.args.get("author", "")
    has_search = bool(request.args)

    # Base query
    q = Book.query.join(Cabinet).join(BookTitle)

    if query:
        q = q.filter(BookTitle.title.contains(query))
    if cabinet_filter:
        q = q.filter(Cabinet.name == cabinet_filter)
    if status_filter == "in":
        q = q.filter(Book.qty_on_hand > 0)
    elif status_filter == "out":
        q = q.filter(Book.qty_on_hand <= 0)
    if author_filter:
        q = q.filter(BookTitle.author.contains(author_filter))

    grouped_books = {}
    authors = {}
    if has_search:
        results = q.all()

        reference_books = []
        if results:
            title_ids = {book.title_id for book in results}
            reference_books = Book.query.filter(Book.title_id.in_(title_ids)).all()
            # Collect authors for display on cards
            for book in results:
                if book.book_title and book.book_title.author:
                    authors[book.title] = book.book_title.author

        grouped_books = build_grouped_book_entries(
            results,
            include_id=True,
            reference_books=reference_books,
            sort_by_stock=True,
            show_counts=False,
        )

    all_cabinets = Cabinet.query.order_by(Cabinet.name).all()
    cabinets_payload = [cabinet_to_dict(cab) for cab in all_cabinets]
    audit_logs = (
        AuditLog.query.order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )
    last_backup_ts = None
    if os.path.exists(LAST_BACKUP_META):
        try:
            with open(LAST_BACKUP_META, "r", encoding="utf-8") as f:
                last_backup_ts = json.load(f).get("last")
        except Exception:
            last_backup_ts = None

    return render_template(
        "admin_dashboard.html",
        grouped_books=grouped_books,
        all_cabinets=all_cabinets,
        cabinets_payload=cabinets_payload,
        audit_logs=audit_logs,
        has_search=has_search,
        last_backup_ts=last_backup_ts,
        authors=authors,
    )


@app.route("/admin/audit")
def audit_page():
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    logs = (
        AuditLog.query.order_by(AuditLog.created_at.desc())
        .limit(200)
        .all()
    )
    return render_template(
        "audit.html",
        title="Audit Trail",
        audit_logs=logs,
        show_top_sellers=False,
    )

@app.route("/toggle/<int:book_id>", methods=["POST"])
def toggle_stock(book_id):
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    book = Book.query.get_or_404(book_id)
    book.qty_on_hand = 0 if (book.qty_on_hand or 0) > 0 else 1
    db.session.commit()
    log_action("toggle_stock", target=book.title, details=f"qty={book.qty_on_hand}")
    db.session.commit()
    export_db_to_csv()  # save to CSV after toggle
    return redirect(url_for("admin_dashboard"))

@app.route("/modify_cabinet/<string:title>", methods=["POST"])
def modify_cabinet(title):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    action = request.form.get("add_or_remove")
    cab_name = request.form.get("cabinet", "").strip()
    if not cab_name:
        return jsonify({"success": False, "message": "請輸入櫃位名稱"})

    cabinet = Cabinet.query.filter_by(name=cab_name).first()
    # Fallback: strip trailing boolean tokens that may be accidentally appended
    if not cabinet:
        simplified = re.sub(r"\s+(true|false)$", "", cab_name, flags=re.IGNORECASE).strip()
        if simplified and simplified != cab_name:
            cabinet = Cabinet.query.filter_by(name=simplified).first()
            if cabinet:
                cab_name = simplified
    if not cabinet and action == "add":
        cabinet = Cabinet(name=cab_name)
        db.session.add(cabinet)
        db.session.commit()
        log_action("create_cabinet_from_book", target=cab_name, details=f"title={title}")
        db.session.commit()

    if not cabinet:
        return jsonify({"success": False, "message": f"櫃位「{cab_name}」不存在"})

    title_obj = get_or_create_title(title)

    # add
    if action == "add":
        existing = Book.query.filter_by(title_id=title_obj.id, cabinet_id=cabinet.id).first()
        book_id = None
        if existing:
            existing.qty_on_hand += 1
            book_id = existing.id
        else:
            new_book = Book(title_id=title_obj.id, cabinet_id=cabinet.id, qty_on_hand=1)
            db.session.add(new_book)
            db.session.flush()
            book_id = new_book.id
        db.session.commit()
        log_action("add_cabinet_to_title", target=title, details=f"cabinet={cab_name}")
        db.session.commit()
        export_db_to_csv()
        return jsonify({
            "success": True,
            "message": f"已將《{title}》 新增至 {cab_name}",
            "action": "add",
            "book_id": book_id,
            "cabinet_id": cabinet.id,
            "cabinet_name": cab_name,
            "title": title,
            "qty_change": 1,
        })

    # remove
    elif action == "remove":
        book = Book.query.filter_by(title_id=title_obj.id, cabinet_id=cabinet.id).first()
        if not book:
            return jsonify({"success": False, "message": f"《{title}》 不存在於 {cab_name}"})

        this_is_display = (cabinet.type or "").strip().lower() == "display"
        other_display_count = (
            Book.query.join(Cabinet)
            .filter(
                Book.title_id == title_obj.id,
                Cabinet.type.ilike("display"),
                Cabinet.id != cabinet.id,
            )
            .count()
        )

        if this_is_display and other_display_count <= 0:
            return jsonify({
                "success": False,
                "message": f"《{title}》於展示櫃將無任何存放！請先新增到另一展示櫃或改為僅切換庫存狀態。"
            }), 400

        qty_removed = book.qty_on_hand or 1
        if book.qty_on_hand > 1:
            book.qty_on_hand -= 1
        else:
            db.session.delete(book)
        db.session.commit()
        log_action("remove_cabinet_from_title", target=title, details=f"cabinet={cab_name}")
        db.session.commit()
        export_db_to_csv()
        return jsonify({
            "success": True,
            "message": f"已將《{title}》 從 {cab_name} 移除",
            "action": "remove",
            "cabinet_id": cabinet.id,
            "cabinet_name": cab_name,
            "title": title,
            "qty_removed": qty_removed,
        })

# 🔍 Search function
@app.route("/cabinets", methods=["GET"])
def list_cabinets():
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    cabinets = Cabinet.query.order_by(Cabinet.name).all()
    return jsonify({"success": True, "cabinets": [cabinet_to_dict(cab) for cab in cabinets]})


def _normalize_cabinet_type(value):
    if not value:
        return None
    norm = value.strip().lower()
    if norm not in {"display", "reserve"}:
        return None
    return norm


@app.route("/cabinets", methods=["POST"])
def create_cabinet():
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    cab_type = _normalize_cabinet_type(payload.get("type")) or "reserve"

    if not name:
        return jsonify({"success": False, "message": "櫃位名稱不可為空"}), 400

    if Cabinet.query.filter_by(name=name).first():
        return jsonify({"success": False, "message": "櫃位名稱已存在"}), 400

    cabinet = Cabinet(name=name, type=cab_type)
    db.session.add(cabinet)
    db.session.commit()
    log_action("create_cabinet", target=name, details=f"type={cab_type}")
    db.session.commit()
    export_db_to_csv()
    return jsonify({
        "success": True,
        "cabinet": cabinet_to_dict(cabinet),
        "affected_titles": [],
    }), 201


@app.route("/cabinets/<int:cabinet_id>", methods=["PATCH"])
def update_cabinet(cabinet_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    cabinet = Cabinet.query.get_or_404(cabinet_id)
    payload = request.get_json(silent=True) or {}

    new_name_raw = payload.get("name")
    new_type_raw = payload.get("type")

    changed = False

    if new_name_raw is not None:
        new_name = new_name_raw.strip()
        if not new_name:
            return jsonify({"success": False, "message": "櫃位名稱不可為空"}), 400
        if new_name != cabinet.name and Cabinet.query.filter_by(name=new_name).first():
            return jsonify({"success": False, "message": "櫃位名稱已存在"}), 400
        if new_name != cabinet.name:
            cabinet.name = new_name
            changed = True

    if new_type_raw is not None:
        norm_type = _normalize_cabinet_type(new_type_raw)
        if not norm_type:
            return jsonify({"success": False, "message": "櫃位類型無效"}), 400
        if cabinet_type_name(cabinet) != norm_type:
            cabinet.type = norm_type
            changed = True

    if not changed:
        return jsonify({
            "success": True,
            "cabinet": cabinet_to_dict(cabinet),
            "affected_titles": [],
        })

    db.session.commit()
    log_action(
        "update_cabinet",
        target=cabinet.name,
        details=f"name={cabinet.name},type={cabinet.type}",
    )
    db.session.commit()
    export_db_to_csv()
    affected_titles = sorted({book.title for book in cabinet.books})
    return jsonify({
        "success": True,
        "cabinet": cabinet_to_dict(cabinet),
        "affected_titles": affected_titles,
    })


@app.route("/cabinets/<int:cabinet_id>", methods=["DELETE"])
def delete_cabinet(cabinet_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    cabinet = Cabinet.query.get_or_404(cabinet_id)
    if cabinet.books:
        return jsonify({"success": False, "message": "櫃位仍有書籍，無法刪除"}), 400

    deleted_payload = {"name": cabinet.name, "type": cabinet.type}
    db.session.delete(cabinet)
    db.session.commit()
    log_action("delete_cabinet", target=cabinet.name)
    db.session.commit()
    export_db_to_csv()
    return jsonify({"success": True, "cabinet_id": cabinet_id, "deleted": deleted_payload})



@app.route("/cabinets/<int:cabinet_id>/books", methods=["GET"])
def list_cabinet_books(cabinet_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    cabinet = Cabinet.query.get_or_404(cabinet_id)
    books = (
        Book.query.filter_by(cabinet_id=cabinet.id)
        .join(BookTitle)
        .order_by(BookTitle.title.asc())
        .all()
    )
    return jsonify(
        {
            "success": True,
            "cabinet": cabinet_to_dict(cabinet),
            "books": [book_to_dict(book) for book in books],
        }
    )


@app.route("/cabinets/<int:cabinet_id>/books/<int:book_id>/toggle", methods=["PATCH"])
def toggle_cabinet_book(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    book = (
        Book.query.filter_by(id=book_id, cabinet_id=cabinet_id)
        .first_or_404()
    )
    book.qty_on_hand = 0 if (book.qty_on_hand or 0) > 0 else 1
    db.session.commit()
    log_action("toggle_cabinet_book", target=book.title, details=f"cabinet_id={cabinet_id},qty={book.qty_on_hand}")
    db.session.commit()
    export_db_to_csv()
    return jsonify(
        {
            "success": True,
            "book": book_to_dict(book),
            "affected_titles": [book.title],
        }
    )


@app.route("/cabinets/<int:cabinet_id>/books/<int:book_id>/adjust", methods=["PATCH"])
def adjust_cabinet_book_quantity(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    payload = request.get_json(silent=True) or {}
    try:
        delta = int(payload.get("delta", 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "delta 必須為數字"}), 400

    book = (
        Book.query.filter_by(id=book_id, cabinet_id=cabinet_id)
        .first_or_404()
    )

    next_qty = (book.qty_on_hand or 0) + delta
    if next_qty <= 0:
        title = book.title
        db.session.delete(book)
        db.session.commit()
        log_action("adjust_quantity_delete", target=title, details=f"cabinet_id={cabinet_id}")
        db.session.commit()
        export_db_to_csv()
        return jsonify({"success": True, "book_id": book_id, "affected_titles": [title]})

    book.qty_on_hand = next_qty
    db.session.commit()
    log_action("adjust_quantity", target=book.title, details=f"cabinet_id={cabinet_id},delta={delta},qty={next_qty}")
    db.session.commit()
    export_db_to_csv()
    return jsonify({"success": True, "book": book_to_dict(book), "affected_titles": [book.title]})

@app.route("/add_book", methods=["POST"])
def add_book():
    # get form values
    title = request.form.get("title", "").strip()
    cabinet_id = request.form.get("cabinet_id", type=int)
    amount = request.form.get("amount", type=int, default=1)

    # validate
    if not title or not cabinet_id:
        return jsonify({"success": False, "message": "缺少書名或櫃位"}), 400

    cabinet = Cabinet.query.get(cabinet_id)
    if not cabinet:
        return jsonify({"success": False, "message": "櫃位不存在"}), 400

    title_obj = get_or_create_title(title)

    existing = Book.query.filter_by(title_id=title_obj.id, cabinet_id=cabinet_id).first()

    created = False
    if existing:
        existing.qty_on_hand = (existing.qty_on_hand or 0) + max(amount, 1)
        db.session.commit()
        log_action("restock_book", target=title_obj.title, details=f"cabinet_id={cabinet_id},amount={amount}")
        db.session.commit()
        export_db_to_csv()
        return jsonify({
            "success": True,
            "message": "已補貨",
            "book_id": existing.id,
            "cabinet_id": cabinet_id,
            "title": title_obj.title,
            "amount_added": max(amount, 1),
            "created": False,
        }), 200
    else:
        new_book = Book(
            title_id=title_obj.id,
            cabinet_id=cabinet_id,
            qty_on_hand=max(amount, 1),
        )
        db.session.add(new_book)
        db.session.commit()
        created = True
        log_action("add_book", target=title_obj.title, details=f"cabinet_id={cabinet_id},amount={amount}")
        db.session.commit()
        export_db_to_csv()
        return jsonify({
            "success": True,
            "message": "書籍已新增",
            "book_id": new_book.id,
            "cabinet_id": cabinet_id,
            "title": title_obj.title,
            "amount_added": max(amount, 1),
            "created": True,
        }), 200


@app.route("/cabinets/<int:cabinet_id>/books/<int:book_id>/move", methods=["PATCH"])
def move_cabinet_book(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    book = (
        Book.query.filter_by(id=book_id, cabinet_id=cabinet_id)
        .first_or_404()
    )

    payload = request.get_json(silent=True) or {}
    target_id_raw = payload.get("target_cabinet_id")
    target_name = (payload.get("target_cabinet_name") or "").strip()

    target = None
    if target_id_raw is not None:
        try:
            target = Cabinet.query.get(int(target_id_raw))
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "目標櫃位格式錯誤"}), 400

    if not target and target_name:
        target = Cabinet.query.filter_by(name=target_name).first()

    if not target:
        return jsonify({"success": False, "message": "目標櫃位不存在"}), 400

    if target.id == cabinet_id:
        return jsonify({"success": False, "message": "目標櫃位與目前櫃位相同"}), 400

    duplicate = Book.query.filter_by(title_id=book.title_id, cabinet_id=target.id).first()
    if duplicate:
        duplicate.qty_on_hand = (duplicate.qty_on_hand or 0) + (book.qty_on_hand or 0)
        db.session.delete(book)
        book = duplicate
    else:
        book.cabinet_id = target.id
    db.session.commit()
    log_action("move_book", target=book.title, details=f"{cabinet_id} -> {target.id}")
    db.session.commit()
    export_db_to_csv()
    return jsonify(
        {
            "success": True,
            "book": book_to_dict(book),
            "source_cabinet_id": cabinet_id,
            "target_cabinet_id": target.id,
            "affected_titles": [book.title],
        }
    )


@app.route("/cabinets/<int:cabinet_id>/books/<int:book_id>", methods=["DELETE"])
def remove_cabinet_book(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    book = (
        Book.query.filter_by(id=book_id, cabinet_id=cabinet_id)
        .first_or_404()
    )
    title = book.title
    qty_removed = book.qty_on_hand or 1
    db.session.delete(book)
    db.session.commit()
    log_action("remove_book_from_cabinet", target=title, details=f"cabinet_id={cabinet_id}")
    db.session.commit()
    export_db_to_csv()
    return jsonify(
        {
            "success": True,
            "book_id": book_id,
            "affected_titles": [title],
            "title": title,
            "cabinet_id": cabinet_id,
            "qty_removed": qty_removed,
        }
    )


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("home"))

    results = Book.query.join(BookTitle).filter(BookTitle.title.contains(query)).all()

    grouped_books = build_grouped_book_entries(
        results,
        include_reserve=False,
        include_reserve_out_of_stock=False,
        show_counts=False,
    )
    covers = {}
    authors = {}
    for book in results:
        title_obj = getattr(book, "book_title", None)
        if title_obj:
            if title_obj.cover_link:
                covers[book.title] = title_obj.cover_link
            if title_obj.author:
                authors[book.title] = title_obj.author

    suggestions = []
    if not results:
        all_books = (
            Book.query.join(BookTitle).join(Cabinet).all()
        )
        profiles = []
        books_by_title = defaultdict(list)
        for b in all_books:
            bt = getattr(b, "book_title", None)
            cab = getattr(b, "cabinet", None)
            profiles.append(
                BookProfile(
                    title=b.title,
                    author=bt.author if bt else "",
                    cabinet=cab.name if cab else "",
                    cabinet_type=(cab.type if cab else "") or "",
                    topics=parse_topics_field(getattr(bt, "topics", None) if bt else None),
                    in_stock=b.in_stock,
                )
            )
            books_by_title[b.title].append(b)

        scored_suggestions = suggest_for_missing_title(profiles, query, top=5)
        if scored_suggestions:
            print("[suggestions]", query)
            for score, prof in scored_suggestions:
                print(f"  {score:.3f} - {prof.title} / {prof.author} ({prof.cabinet})")

        for score, prof in scored_suggestions:
            entries_map = build_grouped_book_entries(
                books_by_title.get(prof.title, []),
                include_reserve=True,
                include_reserve_out_of_stock=True,
                show_counts=False,
            )
            suggestions.append(
                {
                    "title": prof.title,
                    "topics": prof.topics or [],
                    "entries": entries_map.get(prof.title, []),
                    "score": round(score, 3),
                    "author": prof.author or "",
                }
            )

    return render_template(
        "search_results.html",
        grouped_books=grouped_books,
        query=query,
        show_top_sellers=False,   # hide on search page
        covers=covers,
        authors=authors,
        suggestions=suggestions,
    )

@app.route("/book_details/<string:title>")
def book_details(title):
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    books = Book.query.join(BookTitle).filter(BookTitle.title == title).all()
    if not books:
        return jsonify({"error": "Book not found"}), 404

    grouped_map = build_grouped_book_entries(
        books,
        include_id=True,
        reference_books=books,
        include_reserve=True,
        include_reserve_out_of_stock=True,
        include_cabinet_id=True,
    )
    entries = grouped_map.get(title, [])

    modal_html = render_template_string("""
        <div class="modal-content">
        <h2>{{ title }}</h2>
        {% for entry in entries %}
            <div class="modal-row">
            <span>{{ entry.cabinet }}</span>
            <form action="{{ url_for('toggle_modal_stock', id=entry.id) }}" method="post" class="inline-form" data-skip-confirm="true">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button type="submit" class="toggle-btn stat {{ entry.cls }}">
                {{ entry.status }}
                </button>
            </form>
            </div>
            {% if entry.notes %}
            {% for note in entry.notes %}
            <div class="reserve-hint">{{ note }}</div>
            {% endfor %}
            {% endif %}
        {% endfor %}
        <button class="close-btn" onclick="closeModal()">關閉</button>
        </div>
        """, title=title, entries=entries)

    return modal_html


@app.route("/api/title_cabinets/<string:title>")
def title_cabinets(title):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    title_obj = BookTitle.query.filter_by(title=title).first()
    if not title_obj:
        return jsonify({"success": False, "message": "書名不存在"}), 404

    books = Book.query.filter_by(title_id=title_obj.id).join(Cabinet).all()
    payload = []
    for book in books:
        cab = book.cabinet
        payload.append({
            "cabinet": cab.name if cab else "",
            "type": cab.type if cab else "",
            "in_stock": book.in_stock,
        })
    return jsonify({"success": True, "title": title, "cabinets": payload})

@app.route("/book_card/<string:title>")
def book_card(title):
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    books = Book.query.join(BookTitle).filter(BookTitle.title == title).all()
    if not books:
        return "<div class='card'>未找到此書</div>"

    grouped_map = build_grouped_book_entries(books, include_id=True, include_reserve=False)
    grouped = grouped_map.get(title, [])

    return render_template_string("""
    <div class="card" id="card-{{ title }}">
      <div class="card__header">
        <span class="chip chip--soft">書名</span>
        <h3>{{ title }}</h3>
      </div>
      <div class="status-list">
        {% for entry in grouped %}
        <div class="status-row">
          <span class="cab" data-cabinet="{{ entry.cabinet }}">📍 {{ entry.cabinet }}</span>
          <span class="stat {{ entry.cls }}">{{ entry.status }}</span>
        </div>
        {% if entry.notes %}
        {% for note in entry.notes %}
        <div class="reserve-hint">{{ note }}</div>
        {% endfor %}
        {% endif %}
        {% endfor %}
      </div>
      <div class="edit-btn-container btn-group">
        <button type="button" class="edit-btn btn--sm" onclick="openBookModal('{{ title }}')">編輯</button>
        <button type="button" class="mini-btn secondary btn--sm" onclick="openCabinetModal('{{ title }}')">新增 / 移除 櫃位</button>
      </div>
    </div>
    """, title=title, grouped=grouped)

@app.route("/toggle_modal_stock/<int:id>", methods=["POST"])
def toggle_modal_stock(id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    book = Book.query.get_or_404(id)
    book.qty_on_hand = 0 if (book.qty_on_hand or 0) > 0 else 1
    db.session.commit()
    log_action("toggle_modal_stock", target=book.title, details=f"qty={book.qty_on_hand}")
    db.session.commit()
    export_db_to_csv()

    # find which title this book belongs to, for JS to refresh that card
    title = book.title
    return jsonify({
        "success": True,
        "message": f"《{title}》狀態已更新為 {'在庫' if book.in_stock else '缺貨'}",
        "title": title
    })

# 🔐 Admin login page
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = AdminUser.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["is_admin"] = True
            session["admin_user"] = user.username
            session["admin_id"] = user.id
            session["csrf_token"] = secrets.token_urlsafe(32)
            log_action("login_success", target=user.username)
            db.session.commit()
            return redirect(url_for("admin_dashboard"))
        else:
            log_action("login_failed", target=username or "(blank)", details="invalid_credentials")
            db.session.commit()
            error = "Invalid username or password"
    return render_template("login.html", title="Admin Login", error=error)


# Admin registration page
@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        sec_code = (request.form.get("security_code") or "").strip()

        if not username or not password or not email or not sec_code:
            error = "請填寫所有欄位"
        elif len(password) < 6:
            error = "密碼至少 6 碼"
        elif password != confirm:
            error = "密碼確認不一致"
        elif AdminUser.query.filter_by(username=username).first():
            error = "此帳號已存在"
        elif AdminUser.query.filter_by(email=email).first():
            error = "此 Email 已存在"
        else:
            invite = AdminInvite.query.filter_by(code=sec_code, used_at=None).first()
            if not invite:
                error = "安全碼無效或已使用，請向網站擁有者確認"
            else:
                user = AdminUser(
                    username=username,
                    email=email,
                    password_hash=generate_password_hash(password),
                    role="admin",
                )
                db.session.add(user)
                invite.used_at = datetime.utcnow()
                log_action("register_admin", target=username, details=f"email={email}")
                db.session.commit()

            # Auto-login after successful registration
                session["is_admin"] = True
                session["admin_user"] = user.username
                session["admin_id"] = user.id
                session["csrf_token"] = secrets.token_urlsafe(32)
                log_action("login_success", target=user.username, details="auto after register")
                db.session.commit()
                return redirect(url_for("admin_dashboard"))

    return render_template("register.html", title="Admin Register", error=error)

@app.route("/api/notifications")
def get_notifications():
    """Return a live snapshot of system notifications for admin dashboard."""
    if not session.get("is_admin"):
        return jsonify([])  # regular users don’t see notifications

    alerts = collect_replenish_alerts()
    return jsonify(alerts)


@app.route("/titles/<int:title_id>/cover")
def title_cover(title_id):
    """Return cover metadata for a title (public-safe)."""
    title_obj = BookTitle.query.get_or_404(title_id)
    return jsonify({
        "title_id": title_obj.id,
        "title": title_obj.title,
        "cover_url": cover_url_for_title(title_obj),
        "cover_link": title_obj.cover_link,
    })


@app.route("/logout")
def logout():
    actor = session.get("admin_user") or "unknown"
    if session.get("is_admin"):
        log_action("logout", target=actor)
        db.session.commit()
    session.clear()
    return redirect(url_for("home"))


@app.route("/api/view_event", methods=["POST"])
def api_view_event():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "message": "title required"}), 400
    source = (data.get("source") or "").strip() or None
    actor = session.get("admin_user") or (data.get("actor") or "").strip() or None
    log_view_event(title, source=source, actor=actor)
    return jsonify({"success": True})


@app.route("/admin/backup", methods=["POST"])
def admin_backup():
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401
    backups = create_backup()
    with open(LAST_BACKUP_META, "w", encoding="utf-8") as f:
        json.dump({"last": datetime.utcnow().isoformat()}, f)
    log_action("create_backup", target="system", details=f"db={os.path.basename(backups['db'])},csv={os.path.basename(backups['csv'])}")
    db.session.commit()
    return jsonify({"success": True, "message": "備份完成", "backups": backups, "timestamp": backups["timestamp"]})


@app.route("/admin/audit/export")
def export_audit_csv():
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "actor", "action", "target", "details"])
    for log in logs:
        writer.writerow([
            log.created_at.isoformat() if log.created_at else "",
            log.actor or "",
            log.action or "",
            log.target or "",
            (log.details or "").replace("\n", " "),
        ])
    output.seek(0)

    resp = app.response_class(
        response=output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=audit_logs.csv"
        }
    )
    return resp
