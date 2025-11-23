import csv
import os
import secrets
import re
from collections import defaultdict
from sqlalchemy import text
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
from models import db, Book, Cabinet

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

def sync_csv_to_db():
    """Import or update the database from CSV (one-way).

    - Upserts books/cabinets that appear in the CSV
    - Removes books that were deleted from the CSV (keeps cabinets)
    """
    if not os.path.exists(CSV_PATH):
        print(f"[sync_csv_to_db] CSV not found: {CSV_PATH}")
        return

    seen_pairs = set()  # (cabinet_name, title)

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                continue
            cab_name, title, stock_str, *rest = row
            author = rest[0].strip() if rest else ""
            in_stock = stock_str.strip().lower() == "true"
            seen_pairs.add((cab_name, title))

            cabinet = Cabinet.query.filter_by(name=cab_name).first()
            if not cabinet:
                cabinet = Cabinet(name=cab_name)
                db.session.add(cabinet)
                db.session.flush()
            # ensure cabinet has a type set
            if hasattr(cabinet, "type") and not cabinet.type:
                cabinet.type = "display"

            book = Book.query.filter_by(title=title, cabinet_id=cabinet.id).first()
            if not book:
                book = Book(
                    title=title,
                    in_stock=in_stock,
                    cabinet_id=cabinet.id,
                    author=author or None,
                )
                db.session.add(book)
            else:
                book.in_stock = in_stock
                if author:
                    book.author = author

    # Remove books no longer present in the CSV (by cabinet name + title pair)
    for book in Book.query.join(Cabinet).all():
        pair = (book.cabinet.name if book.cabinet else "", book.title)
        if pair not in seen_pairs:
            db.session.delete(book)

    db.session.commit()
    print("[sync_csv_to_db] CSV -> DB sync complete.")

def export_db_to_csv():
    """Export database back to CSV (one-way)."""
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for cab in Cabinet.query.all():
            for book in cab.books:
                writer.writerow([
                    cab.name,
                    book.title,
                    str(book.in_stock),
                    book.author or "",
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
    """Ensure the book table has an author column (manual migration for SQLite)."""
    with db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(book)"))
        columns = [row[1] for row in result]
    if "author" not in columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE book ADD COLUMN author TEXT"))


def initialize_app():
    """Run one-time startup tasks."""
    with app.app_context():
        db.create_all()
        ensure_cabinet_type_column()
        ensure_author_column()
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
        "book_count": len(cabinet.books),
    }


def book_to_dict(book):
    """Serialize a book record for JSON responses."""
    return {
        "id": book.id,
        "title": book.title,
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
    """Remove reserve cabinet entries with no stock."""
    stale_books = (
        Book.query.join(Cabinet)
        .filter(Book.in_stock.is_(False))
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
        any_in_stock = any(ref.in_stock for ref in reference_list)
        all_in_stock = all(ref.in_stock for ref in reference_list)
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

            in_stock = book.in_stock
            status = (
                "🟢 在庫"
                if in_stock
                else ("🔴 缺貨" if not any_in_stock else "🟠 無庫存")
            )
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

    # 1️⃣ Books marked out of stock but have reserve copies
    from models import Book, Cabinet

    out_books = Book.query.filter_by(in_stock=False).all()
    for book in out_books:
        # Check if same title exists in a reserve cabinet
        reserve_copy = Book.query.filter_by(title=book.title).join(Cabinet).filter(Cabinet.type == "reserve").first()
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
    reserve_books = Book.query.join(Cabinet).filter(Cabinet.type == "reserve").all()
    for book in reserve_books:
        display_copy = Book.query.filter_by(title=book.title).join(Cabinet).filter(Cabinet.type == "display").first()
        if not display_copy:
            alerts.append({
                "type": "low-stock",
                "message": f"《{book.title}》僅存在備書櫃，未展示"
            })

    # 3️⃣ Empty cabinets (no books)
    empty_cabs = []
    for cab in Cabinet.query.all():
        has_books = Book.query.filter_by(cabinet_id=cab.id).first()
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
    q = Book.query.join(Cabinet)

    if query:
        q = q.filter(Book.title.contains(query))
    if cabinet_filter:
        q = q.filter(Cabinet.name == cabinet_filter)
    if status_filter == "in":
        q = q.filter(Book.in_stock.is_(True))
    elif status_filter == "out":
        q = q.filter(Book.in_stock.is_(False))
    if author_filter:
        q = q.filter(Book.author.contains(author_filter))

    results = q.all()

    reference_books = []
    if results:
        title_set = {book.title for book in results}
        reference_books = Book.query.filter(Book.title.in_(title_set)).all()

    grouped_books = build_grouped_book_entries(
        results,
        include_id=True,
        reference_books=reference_books,
        sort_by_stock=True,
    )

    all_cabinets = Cabinet.query.order_by(Cabinet.name).all()
    cabinets_payload = [cabinet_to_dict(cab) for cab in all_cabinets]

    return render_template(
        "admin_dashboard.html",
        grouped_books=grouped_books,
        all_cabinets=all_cabinets,
        cabinets_payload=cabinets_payload,
    )

@app.route("/toggle/<int:book_id>", methods=["POST"])
def toggle_stock(book_id):
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    book = Book.query.get_or_404(book_id)
    book.in_stock = not book.in_stock
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

    if not cabinet:
        return jsonify({"success": False, "message": f"櫃位「{cab_name}」不存在"})

    # add
    if action == "add":
        existing = Book.query.filter_by(title=title, cabinet_id=cabinet.id).first()
        if existing:
            return jsonify({"success": False, "message": f"《{title}》 已存在於 {cab_name}"})
        db.session.add(Book(title=title, in_stock=True, cabinet_id=cabinet.id))
        db.session.commit()
        export_db_to_csv()
        return jsonify({"success": True, "message": f"已將《{title}》 新增至 {cab_name}"})

        # remove
    elif action == "remove":
        book = Book.query.filter_by(title=title, cabinet_id=cabinet.id).first()
        if not book:
            return jsonify({"success": False, "message": f"《{title}》 不存在於 {cab_name}"})

        # 🚫 Guard: don't allow removing the last DISPLAY copy
        # Count remaining DISPLAY copies of this title (excluding the one we’re deleting)
        remaining_display = (
            Book.query.join(Cabinet)
            .filter(
                Book.title == title,
                Book.id != book.id,
                Cabinet.type == "display",
            )
            .count()
        )
        # Is the current cabinet a DISPLAY one?
        this_is_display = (cabinet.type or "").strip().lower() == "display"

        if this_is_display and remaining_display == 0:
            return jsonify({
                "success": False,
                "message": f"《{title}》於展示櫃將無任何存放！請先新增到另一展示櫃或改為僅切換庫存狀態。"
            }), 400

        db.session.delete(book)
        db.session.commit()
        export_db_to_csv()
        return jsonify({"success": True, "message": f"已將《{title}》 從 {cab_name} 移除"})

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

    db.session.delete(cabinet)
    db.session.commit()
    export_db_to_csv()
    return jsonify({"success": True, "cabinet_id": cabinet_id})



@app.route("/cabinets/<int:cabinet_id>/books", methods=["GET"])
def list_cabinet_books(cabinet_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    cabinet = Cabinet.query.get_or_404(cabinet_id)
    books = (
        Book.query.filter_by(cabinet_id=cabinet.id)
        .order_by(Book.title.asc())
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
    book.in_stock = not book.in_stock
    db.session.commit()
    export_db_to_csv()
    return jsonify(
        {
            "success": True,
            "book": book_to_dict(book),
            "affected_titles": [book.title],
        }
    )

@app.route("/add_book", methods=["POST"])
def add_book():
    # get form values
    title = request.form.get("title", "").strip()
    cabinet_id = request.form.get("cabinet_id", type=int)
    amount = request.form.get("amount", type=int, default=1)

    # validate
    if not title or not cabinet_id:
        return jsonify({"success": False, "message": "缺少書名或櫃位"}), 400

    # query by id
    existing = Book.query.filter_by(title=title, cabinet_id=cabinet_id).first()

    if existing:
        # just mark it as restocked / increment if you track quantities
        existing.in_stock = True
        db.session.commit()
        return jsonify({"success": True, "message": "已補貨"}), 200
    else:
        new_book = Book(
            title=title,
            cabinet_id=cabinet_id,
            in_stock=True,
        )
        db.session.add(new_book)
        db.session.commit()
        return jsonify({"success": True, "message": "書籍已新增"}), 200


@app.route("/cabinets/<int:cabinet_id>/books/<int:book_id>/move", methods=["PATCH"])
def move_cabinet_book(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "���n�J"}), 401

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

    duplicate = Book.query.filter_by(title=book.title, cabinet_id=target.id).first()
    if duplicate:
        return jsonify({"success": False, "message": "該櫃位已存在同名書籍"}), 400

    book.cabinet_id = target.id
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
    db.session.delete(book)
    db.session.commit()
    export_db_to_csv()
    return jsonify(
        {
            "success": True,
            "book_id": book_id,
            "affected_titles": [title],
        }
    )


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("home"))

    results = Book.query.filter(Book.title.contains(query)).all()

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

    books = Book.query.filter_by(title=title).all()
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
            <form action="{{ url_for('toggle_modal_stock', id=entry.id) }}" method="post" class="inline-form">
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

    books = Book.query.filter_by(title=title).all()
    if not books:
        return "<div class='card'>未找到此書</div>"

    grouped_map = build_grouped_book_entries(books, include_id=True, include_reserve=False)
    grouped = grouped_map.get(title, [])

    return render_template_string("""
    <div class="card" id="card-{{ title }}">
      <h3>{{ title }}</h3>
      <div class="status-list">
        {% for entry in grouped %}
        <div class="status-row">
          <span class="cab">{{ entry.cabinet }}</span>
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
    book.in_stock = not book.in_stock
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
