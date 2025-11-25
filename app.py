import csv
import os
import secrets
import re
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
from models import db, Book, Cabinet, BookTitle, Inventory, AuditLog

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "database")
os.makedirs(DATA_DIR, exist_ok=True)
CSV_PATH = os.path.join(DATA_DIR, "inventory.csv")
DB_PATH = os.path.join(DATA_DIR, "inventory.db")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
_plain_password = os.environ.get("ADMIN_PASSWORD")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = generate_password_hash(_plain_password or "1234")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("APP_SECRET_KEY")
    or secrets.token_hex(32)
)
db.init_app(app)

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
    if not os.path.exists(CSV_PATH):
        print(f"[sync_csv_to_db] CSV not found: {CSV_PATH}")
        return

    aggregates = Counter()  # (cabinet_name, title) -> qty
    authors = {}

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                continue
            cab_name, title, qty_str, *rest = row
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


def export_db_to_csv():
    """Export database back to CSV (one-way)."""
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for cab in Cabinet.query.all():
            for inv in cab.books:
                writer.writerow([
                    cab.name,
                    inv.title,
                    str(inv.qty_on_hand),
                    inv.author or "",
                ])
    print("[export_db_to_csv] DB -> CSV export complete.")


def ensure_cabinet_type_column():
    """Ensure cabinet table has a type column for main/reserve tagging."""
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
    with db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(book)"))
        columns = [row[1] for row in result]
    if "author" not in columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE book ADD COLUMN author TEXT"))


def migrate_legacy_books_into_inventory():
    """One-time migration: move rows from old book table into new normalized tables."""
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
    reference_books=None,
    include_reserve=True,
    include_reserve_out_of_stock=False,
    sort_by_stock=False,
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

        note_text = None
        if reserve_sources and has_display_out:
            joined = "、".join(reserve_sources)
            note_text = f"📦 請取{joined}"

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
            if in_stock:
                status = (
                    f"🟢 在庫（{qty} 本）"
                    if qty > LOW_STOCK_THRESHOLD
                    else f"🟠 低庫存（{qty} 本）"
                )
            else:
                status = "🔴 缺貨" if not any_in_stock else "🟠 無庫存"
            entry = {
                "cabinet": cabinet_name,
                "status": status,
                "cls": "in-stock" if in_stock else "out-stock",
                "notes": [],
            }
            if include_id:
                entry["id"] = book.id

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
    return render_template("home.html", title="書展庫存系統", show_top_sellers=True)

@app.route("/admin")
def admin_dashboard():
    query = request.args.get("filter", "").strip()
    cabinet_filter = request.args.get("cabinet", "")
    status_filter = request.args.get("status", "")
    author_filter = request.args.get("author", "")

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

    results = q.all()

    reference_books = []
    if results:
        title_ids = {book.title_id for book in results}
        reference_books = Book.query.filter(Book.title_id.in_(title_ids)).all()

    grouped_books = build_grouped_book_entries(
        results,
        include_id=True,
        reference_books=reference_books,
        sort_by_stock=True,
    )

    all_cabinets = Cabinet.query.order_by(Cabinet.name).all()
    cabinets_payload = [cabinet_to_dict(cab) for cab in all_cabinets]
    audit_logs = (
        AuditLog.query.order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "admin_dashboard.html",
        grouped_books=grouped_books,
        all_cabinets=all_cabinets,
        cabinets_payload=cabinets_payload,
        audit_logs=audit_logs,
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

    grouped_books = build_grouped_book_entries(results)

    return render_template(
        "search_results.html",
        grouped_books=grouped_books,
        query=query,
        show_top_sellers=False   # hide on search page
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
        include_reserve=False,
        include_reserve_out_of_stock=True,
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
                <button type="submit" class="toggle-btn {{ entry.cls }}">
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
        if (
            username == ADMIN_USERNAME
            and check_password_hash(ADMIN_PASSWORD_HASH, password)
        ):
            session["is_admin"] = True
            session["admin_user"] = username or ADMIN_USERNAME
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Invalid username or password"
    return render_template("login.html", title="Admin Login", error=error)

@app.route("/api/notifications")
def get_notifications():
    """Return a live snapshot of system notifications for admin dashboard."""
    if not session.get("is_admin"):
        return jsonify([])  # regular users don’t see notifications

    alerts = collect_replenish_alerts()
    return jsonify(alerts)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))
