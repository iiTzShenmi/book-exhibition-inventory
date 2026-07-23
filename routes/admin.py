import csv
import io
import json
import math
import os
import re
import secrets
import difflib
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func
from werkzeug.security import check_password_hash

from database.models import AuditLog, Book, BookTitle, Cabinet, EventSchedule, BackupArchive, Inventory, AdminUser, FloorPlanObject, FloorPlanPosition, IssueReport, db
from similarity import parse_topics_field
from app import (
    active_books_query,
    build_grouped_book_entries,
    cabinet_to_dict,
    cabinet_type_name,
    csv_safe_row,
    log_action,
    collect_replenish_alerts,
    FLOOR_PLAN_OBJECT_KINDS,
    ensure_default_floor_plan_objects,
    floor_plan_objects,
    floor_plan_layout_for_cabinets,
    normalize_cover_url,
    parse_qty,
    sync_csv_to_db,
)


admin_bp = Blueprint("admin", __name__)


IMPORT_ARTIFACT_MAX_AGE_SECONDS = 60 * 60
FLOOR_PLAN_POSITION_FIELDS = {"cabinet_id", "left", "top", "width", "height"}
FLOOR_PLAN_MAX_POSITIONS = 80
FLOOR_PLAN_OBJECT_FIELDS = {"object_key", "kind", "label", "left", "top", "width", "height"}
FLOOR_PLAN_MAX_OBJECTS = 30
FLOOR_PLAN_OBJECT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _parse_floor_plan_position(payload):
    if not isinstance(payload, dict) or set(payload) != FLOOR_PLAN_POSITION_FIELDS:
        return None

    cabinet_id = payload.get("cabinet_id")
    if isinstance(cabinet_id, bool) or not isinstance(cabinet_id, int) or cabinet_id <= 0:
        return None

    values = []
    for key in ("left", "top", "width", "height"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        values.append(float(value))

    left, top, width, height = values
    if (
        left < 0 or top < 0 or width <= 0 or height <= 0
        or width > 100 or height > 100
        or left + width > 100 or top + height > 100
    ):
        return None
    return cabinet_id, left, top, width, height


def _normalize_floor_plan_label(value):
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > 80:
        return None
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        return None
    return normalized


def _parse_floor_plan_object(payload):
    if not isinstance(payload, dict) or set(payload) != FLOOR_PLAN_OBJECT_FIELDS:
        return None
    object_key = payload.get("object_key")
    kind = payload.get("kind")
    label = _normalize_floor_plan_label(payload.get("label"))
    if (
        not isinstance(object_key, str)
        or not FLOOR_PLAN_OBJECT_KEY_PATTERN.fullmatch(object_key)
        or kind not in FLOOR_PLAN_OBJECT_KINDS
        or not label
    ):
        return None

    values = []
    for key in ("left", "top", "width", "height"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        values.append(float(value))
    left, top, width, height = values
    if (
        left < 0 or top < 0 or width <= 0 or height <= 0
        or width > 100 or height > 100
        or left + width > 100 or top + height > 100
    ):
        return None
    return object_key, kind, label, left, top, width, height


def _import_artifact_dir() -> str:
    return os.path.join(current_app.root_path, "database", "imports")


def _cleanup_import_artifacts(max_age_seconds: int = IMPORT_ARTIFACT_MAX_AGE_SECONDS) -> None:
    import_dir = _import_artifact_dir()
    if not os.path.isdir(import_dir):
        return
    now_ts = time.time()
    allowed_suffixes = (".csv", ".meta.json", ".warnings.csv")
    for name in os.listdir(import_dir):
        if not name.endswith(allowed_suffixes):
            continue
        path = os.path.join(import_dir, name)
        try:
            if now_ts - os.path.getmtime(path) > max_age_seconds:
                os.remove(path)
        except OSError:
            continue


def _write_external_backup_copy(filename: str, csv_content: str) -> str:
    export_dir = (os.environ.get("EXIS_BACKUP_EXPORT_DIR") or "").strip()
    if not export_dir:
        return ""
    output_dir = os.path.abspath(os.path.expanduser(export_dir))
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, os.path.basename(filename))
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        f.write(csv_content)
    return output_path


def _current_admin_user():
    if not session.get("is_admin"):
        return None
    admin_id = session.get("admin_id")
    if admin_id:
        user = AdminUser.query.get(admin_id)
        if user:
            return user
    username = session.get("admin_user")
    if username:
        return AdminUser.query.filter_by(username=username).first()
    return None


def _role_allows(role: str | None, allowed_roles: set[str]) -> bool:
    role = role or "admin"
    if role == "advance-admin":
        return True
    return role in allowed_roles


def _require_roles(*roles: str, json_response: bool = False):
    user = _current_admin_user()
    if not user:
        if json_response:
            return None, (jsonify({"success": False, "message": "未登入"}), 401)
        return None, redirect(url_for("auth.login"))
    if not _role_allows(user.role, set(roles)):
        if json_response:
            return None, (jsonify({"success": False, "message": "權限不足"}), 403)
        return None, redirect(url_for("admin.dashboard"))
    return user, None


def _parse_book_ids(raw_value: str):
    if not raw_value:
        return []
    raw_value = raw_value.strip()
    if not raw_value:
        return []
    if raw_value.startswith("["):
        try:
            data = json.loads(raw_value)
            return [int(x) for x in data if str(x).isdigit()]
        except json.JSONDecodeError:
            return []
    ids = []
    for part in raw_value.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def _create_backup_archive(note: str | None = None) -> BackupArchive:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Cabinet", "Title", "InStock", "Author", "Status", "Updated"])

    inventories = (
        Inventory.query.join(Cabinet).join(BookTitle)
        .filter(Inventory.status == "active")
        .all()
    )
    count = 0
    for inv in inventories:
        writer.writerow(csv_safe_row([
            inv.cabinet.name if inv.cabinet else "",
            inv.book_title.title if inv.book_title else "",
            "TRUE" if inv.in_stock else "FALSE",
            inv.book_title.author if inv.book_title and inv.book_title.author else "",
            inv.status,
            inv.updated_at.isoformat() if inv.updated_at else "",
        ]))
        count += 1

    csv_string = output.getvalue()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.csv"
    backup_note = note or f"Auto backup: {count} books"
    local_export_path = _write_external_backup_copy(filename, csv_string)
    if local_export_path:
        backup_note = f"{backup_note}; local_export={local_export_path}"

    new_backup = BackupArchive(
        filename=filename,
        csv_content=csv_string,
        note=backup_note,
    )
    db.session.add(new_backup)
    db.session.flush()
    return new_backup


def _normalize_title_for_compare(text: str) -> str:
    """Normalize titles for warning comparison (strip full-width punctuation, dashes, underscores)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    full_width_punct = "，。！？：；、（）「」『』【】《》〈〉［］〔〕…．・—–－"
    cleaned = re.sub(f"[{re.escape(full_width_punct)}]", " ", cleaned)
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    cleaned = re.sub(r"\\s+", " ", cleaned).strip()
    return cleaned


@admin_bp.route("/admin/add_book_preview", methods=["POST"])
def add_book_preview():
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    title = (request.form.get("title") or "").strip()
    cabinet_id = request.form.get("cabinet_id", type=int)
    if not title:
        return jsonify({"success": False, "message": "缺少書名"}), 400

    normalized = _normalize_title_for_compare(title)
    existing_titles = BookTitle.query.all()
    existing_norm_map = {}
    for bt in existing_titles:
        norm = _normalize_title_for_compare(bt.title)
        if norm:
            existing_norm_map.setdefault(norm, bt)

    exact_match = existing_norm_map.get(normalized)
    similar_titles = []
    if not exact_match and existing_norm_map:
        matches = difflib.get_close_matches(normalized, list(existing_norm_map.keys()), n=3, cutoff=0.86)
        similar_titles = [existing_norm_map[m].title for m in matches]
    elif exact_match and exact_match.title != title:
        similar_titles = [exact_match.title]

    existing_in_cabinet = False
    if exact_match and cabinet_id:
        existing_in_cabinet = (
            Inventory.query
            .filter_by(title_id=exact_match.id, cabinet_id=cabinet_id)
            .first()
            is not None
        )

    author = (exact_match.author or "") if exact_match else ""
    cover_url = normalize_cover_url(exact_match.cover_link if exact_match else "")
    topics = parse_topics_field((exact_match.topics if exact_match else None)) or []

    if not author:
        try:
            from tools.fetch_author import fetch_author_for_title
            author, _ = fetch_author_for_title(title)
            author = author or ""
        except Exception:
            author = ""

    if not cover_url:
        try:
            from tools.fetch_cover_url import fetch_url_for_title
            cover_url, _ = fetch_url_for_title(title)
            cover_url = normalize_cover_url(cover_url)
        except Exception:
            cover_url = ""

    if not topics:
        try:
            from tools.fetch_topics import fetch_topics_for_title
            topics, _ = fetch_topics_for_title(title)
            topics = topics or []
        except Exception:
            topics = []

    return jsonify({
        "success": True,
        "title": title,
        "author": author or "",
        "cover_url": normalize_cover_url(cover_url),
        "topics": topics or [],
        "exact_match_title": exact_match.title if exact_match else "",
        "similar_titles": similar_titles,
        "existing_in_cabinet": existing_in_cabinet,
    })


@admin_bp.route("/admin")
def dashboard():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    query = request.args.get("filter", "").strip()
    cabinet_filter = request.args.get("cabinet", "")
    status_filter = request.args.get("status", "")
    author_filter = request.args.get("author", "")
    has_search = bool(request.args)

    q = active_books_query().join(Cabinet).join(BookTitle)

    if query:
        q = q.filter(BookTitle.title.contains(query))
    if cabinet_filter:
        q = q.filter(Cabinet.name == cabinet_filter)
    if status_filter == "in":
        pass
    elif status_filter == "out":
        q = q.filter(False)
    if author_filter:
        q = q.filter(BookTitle.author.contains(author_filter))

    grouped_books = {}
    authors = {}
    if has_search:
        results = q.all()

        reference_books = []
        if results:
            title_ids = {book.title_id for book in results}
            reference_books = active_books_query().filter(Book.title_id.in_(title_ids)).all()
            for book in results:
                if book.book_title and book.book_title.author:
                    authors[book.title] = book.book_title.author

        grouped_books = build_grouped_book_entries(
            results,
            include_id=True,
            reference_books=reference_books,
            include_reserve=True,
            include_reserve_out_of_stock=True,
            sort_by_stock=True,
            show_counts=False,
        )

    alerts = collect_replenish_alerts()
    alert_priority = {"out-of-stock": 0, "low-stock": 1}
    dashboard_alerts = sorted(
        [alert for alert in alerts if alert.get("type") in alert_priority],
        key=lambda alert: alert_priority.get(alert.get("type"), 99),
    )[:10]

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
        has_search=has_search,
        authors=authors,
        dashboard_alerts=dashboard_alerts,
    )


@admin_bp.route("/admin/overview")
def overview():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    total_titles = BookTitle.query.count()
    total_inventory = Inventory.query.filter(Inventory.status == "active").count()
    out_of_stock = Inventory.query.filter(
        Inventory.status == "active",
        Inventory.in_stock.is_(False),
    ).count()
    cabinets_count = Cabinet.query.count()
    active_events = EventSchedule.query.filter(EventSchedule.is_active.is_(True)).count()
    alerts = collect_replenish_alerts()

    return render_template(
        "admin_overview.html",
        title="概覽",
        total_titles=total_titles,
        total_inventory=total_inventory,
        out_of_stock=out_of_stock,
        cabinets_count=cabinets_count,
        active_events=active_events,
        alerts=alerts,
        show_top_sellers=False,
    )


@admin_bp.route("/admin/floor-plan")
def floor_plan_editor():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    ensure_default_floor_plan_objects()
    cabinets = Cabinet.query.order_by(Cabinet.name.asc()).all()
    return render_template(
        "admin_floor_plan.html",
        title="平面圖編輯",
        floor_plan_layout=floor_plan_layout_for_cabinets(cabinets),
        floor_plan_objects=floor_plan_objects(),
        show_top_sellers=False,
    )


@admin_bp.route("/admin/floor-plan/layout", methods=["POST"])
def save_floor_plan_layout():
    _, response = _require_roles("admin", "manager", json_response=True)
    if response:
        return response
    if not request.is_json:
        return jsonify({"success": False, "message": "平面圖資料必須使用 JSON。"}), 415

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) not in ({"positions"}, {"positions", "objects"}):
        return jsonify({"success": False, "message": "平面圖資料格式不正確。"}), 400
    positions = payload.get("positions")
    if not isinstance(positions, list) or len(positions) > FLOOR_PLAN_MAX_POSITIONS:
        return jsonify({"success": False, "message": "平面圖位置數量不正確。"}), 400

    parsed_positions = []
    cabinet_ids = set()
    for position in positions:
        parsed = _parse_floor_plan_position(position)
        if not parsed or parsed[0] in cabinet_ids:
            return jsonify({"success": False, "message": "平面圖座標不正確。"}), 400
        cabinet_ids.add(parsed[0])
        parsed_positions.append(parsed)

    raw_objects = payload.get("objects")
    parsed_objects = None
    if raw_objects is not None:
        if not isinstance(raw_objects, list) or not 1 <= len(raw_objects) <= FLOOR_PLAN_MAX_OBJECTS:
            return jsonify({"success": False, "message": "周邊物件數量不正確。"}), 400
        parsed_objects = []
        object_keys = set()
        for floor_object in raw_objects:
            parsed = _parse_floor_plan_object(floor_object)
            if not parsed or parsed[0] in object_keys:
                return jsonify({"success": False, "message": "周邊物件資料不正確。"}), 400
            object_keys.add(parsed[0])
            parsed_objects.append(parsed)

    cabinets = Cabinet.query.filter(Cabinet.id.in_(cabinet_ids)).all() if cabinet_ids else []
    cabinets_by_id = {cabinet.id: cabinet for cabinet in cabinets}
    if len(cabinets_by_id) != len(cabinet_ids) or any(
        cabinet_type_name(cabinet) != "display" for cabinet in cabinets_by_id.values()
    ):
        return jsonify({"success": False, "message": "只能配置展示櫃。"}), 400

    try:
        existing_positions = (
            FloorPlanPosition.query.filter(FloorPlanPosition.cabinet_id.in_(cabinet_ids)).all()
            if cabinet_ids
            else []
        )
        existing_by_cabinet = {position.cabinet_id: position for position in existing_positions}
        for cabinet_id, left, top, width, height in parsed_positions:
            position = existing_by_cabinet.get(cabinet_id)
            if position is None:
                position = FloorPlanPosition(cabinet_id=cabinet_id)
                db.session.add(position)
            position.left_percent = left
            position.top_percent = top
            position.width_percent = width
            position.height_percent = height
        removed_object_count = 0
        if parsed_objects is not None:
            existing_objects = FloorPlanObject.query.all()
            existing_by_key = {floor_object.object_key: floor_object for floor_object in existing_objects}
            submitted_keys = {floor_object[0] for floor_object in parsed_objects}
            for floor_object in existing_objects:
                if floor_object.object_key not in submitted_keys:
                    db.session.delete(floor_object)
                    removed_object_count += 1
            for object_key, kind, label, left, top, width, height in parsed_objects:
                floor_object = existing_by_key.get(object_key)
                if floor_object is None:
                    floor_object = FloorPlanObject(object_key=object_key)
                    db.session.add(floor_object)
                floor_object.kind = kind
                floor_object.label = label
                floor_object.left_percent = left
                floor_object.top_percent = top
                floor_object.width_percent = width
                floor_object.height_percent = height
        if parsed_positions or parsed_objects is not None:
            log_action(
                "update_floor_plan",
                target="floor_plan",
                details=(
                    f"positions={len(parsed_positions)},"
                    f"objects={len(parsed_objects) if parsed_objects is not None else 0},"
                    f"objects_removed={removed_object_count}"
                ),
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("floor plan update failed")
        return jsonify({"success": False, "message": "平面圖儲存失敗，請稍後再試。"}), 500

    all_cabinets = Cabinet.query.order_by(Cabinet.name.asc()).all()
    return jsonify({
        "success": True,
        "layout": floor_plan_layout_for_cabinets(all_cabinets),
        "objects": floor_plan_objects(),
    })


@admin_bp.route("/admin/floor-plan/layout/<int:cabinet_id>", methods=["DELETE"])
def reset_floor_plan_position(cabinet_id):
    _, response = _require_roles("admin", "manager", json_response=True)
    if response:
        return response

    cabinet = db.session.get(Cabinet, cabinet_id)
    if not cabinet or cabinet_type_name(cabinet) != "display":
        return jsonify({"success": False, "message": "找不到展示櫃。"}), 404

    try:
        position = FloorPlanPosition.query.filter_by(cabinet_id=cabinet.id).first()
        if position:
            db.session.delete(position)
            log_action("reset_floor_plan", target=str(cabinet.id), details="restored default position")
            db.session.commit()
        layout = floor_plan_layout_for_cabinets([cabinet])
    except Exception:
        db.session.rollback()
        current_app.logger.exception("floor plan reset failed")
        return jsonify({"success": False, "message": "平面圖重設失敗，請稍後再試。"}), 500

    return jsonify({"success": True, "position": layout[0]})


@admin_bp.route("/admin/system")
def system_page():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    _cleanup_import_artifacts()

    admin_user = _current_admin_user()
    can_view_sensitive = bool(admin_user and _role_allows(admin_user.role, {"advance-admin"}))
    can_upload = can_view_sensitive

    logs = []
    recent_backups = []
    issue_reports = []
    if can_view_sensitive:
        logs = (
            AuditLog.query.order_by(AuditLog.created_at.desc())
            .limit(200)
            .all()
        )
        recent_backups = BackupArchive.query.order_by(BackupArchive.created_at.desc()).limit(5).all()
        issue_reports = IssueReport.query.order_by(IssueReport.created_at.desc()).limit(200).all()

    return render_template(
        "admin_system.html",
        title="系統",
        audit_logs=logs,
        recent_backups=recent_backups,
        issue_reports=issue_reports,
        can_upload=can_upload,
        can_view_sensitive=can_view_sensitive,
        show_top_sellers=False,
    )


@admin_bp.route("/admin/backups")
def backup_page():
    _, response = _require_roles("advance-admin")
    if response:
        return response
    return redirect(url_for("admin.system_page"))




@admin_bp.route("/admin/audit")
def audit_page():
    _, response = _require_roles("advance-admin")
    if response:
        return response

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


@admin_bp.route("/admin/backup", methods=["POST"])
def admin_backup():
    _, response = _require_roles("advance-admin", json_response=True)
    if response:
        return response
    try:
        new_backup = _create_backup_archive(note="Manual backup")
        log_action("create_db_backup", target="system", details=f"saved {new_backup.filename} to DB")
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("manual database backup failed")
        return jsonify({"success": False, "message": "備份失敗，請稍後再試。"}), 500
    return jsonify({
        "success": True,
        "message": "備份已成功儲存至資料庫",
        "backup": new_backup.to_dict(),
        "dr_note": (
            "已同時寫出指定本機掛載目錄；此副本本身不構成災難復原。"
            if os.environ.get("EXIS_BACKUP_EXPORT_DIR")
            else "資料庫內建備份僅供快速回復；災難復原請使用 Render PITR 或排程的物件儲存邏輯備份。"
        ),
        "timestamp": new_backup.created_at.isoformat() if new_backup.created_at else "",
    })


@admin_bp.route("/admin/import/preview", methods=["POST"])
def admin_import_preview():
    _, response = _require_roles("advance-admin")
    if response:
        return response
    _cleanup_import_artifacts()

    upload = request.files.get("csv_file")
    if not upload or not upload.filename:
        return redirect(url_for("admin.system_page"))
    if not upload.filename.lower().endswith(".csv"):
        return redirect(url_for("admin.system_page"))

    max_upload_bytes = current_app.config.get("MAX_CONTENT_LENGTH") or (5 * 1024 * 1024)
    raw_upload = upload.read()
    if len(raw_upload) > max_upload_bytes:
        current_app.logger.warning(
            "CSV upload rejected because it exceeded MAX_CONTENT_LENGTH: size=%s max=%s",
            len(raw_upload),
            max_upload_bytes,
        )
        return redirect(url_for("admin.system_page"))

    try:
        csv_text = raw_upload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return redirect(url_for("admin.system_page"))
    if not csv_text.strip():
        return redirect(url_for("admin.system_page"))

    def is_header(row):
        if len(row) < 2:
            return False
        first = row[0].strip().lstrip("\ufeff").lower()
        second = row[1].strip().lower()
        return first in {"cabinet", "cabinet_name", "櫃位"} and second in {"title", "book", "書名"}

    aggregates = {}
    row_counts = Counter()
    title_variants = defaultdict(set)
    cabinets_seen = set()
    invalid_rows = []

    reader = csv.reader(io.StringIO(csv_text))
    for row in reader:
        if len(row) < 2:
            continue
        if is_header(row):
            continue
        cab_name, title, *rest = row
        qty_raw = rest[0] if len(rest) >= 1 else None
        rest = rest[1:] if len(rest) >= 2 else []
        cab_name = (cab_name or "").lstrip("\ufeff").strip()
        title = (title or "").lstrip("\ufeff").strip()
        author = (rest[0].strip() if rest else "") or None
        if not cab_name or not title:
            invalid_rows.append(row)
            continue
        normalized = _normalize_title_for_compare(title)
        key = (cab_name, normalized)
        cabinets_seen.add(cab_name)
        row_counts[key] += 1
        title_variants[key].add(title)
        qty_raw_str = "" if qty_raw is None else str(qty_raw).strip()
        qty = parse_qty(qty_raw) if qty_raw_str else 1
        entry = aggregates.get(key)
        if not entry:
            aggregates[key] = {
                "cabinet": cab_name,
                "title": title,
                "normalized": normalized,
                "author": author or "",
                "qty": qty,
            }
        else:
            entry["qty"] += qty
            if not entry["author"] and author:
                entry["author"] = author

    existing_titles = BookTitle.query.with_entities(BookTitle.title).all()
    existing_titles = [row[0] for row in existing_titles if row and row[0]]
    existing_norm_map = {}
    for title in existing_titles:
        norm = _normalize_title_for_compare(title)
        existing_norm_map.setdefault(norm, title)
    existing_norms = list(existing_norm_map.keys())
    existing_cabinets = {cab.name for cab in Cabinet.query.all()}
    new_cabinets = sorted(name for name in cabinets_seen if name not in existing_cabinets)

    preview_rows = []
    new_titles = 0
    existing_titles_count = 0
    cabinet_create_count = len(new_cabinets)

    import_dir = _import_artifact_dir()
    os.makedirs(import_dir, exist_ok=True)
    token = secrets.token_urlsafe(16)
    file_path = os.path.join(import_dir, f"{token}.csv")
    meta_path = os.path.join(import_dir, f"{token}.meta.json")
    warnings_path = os.path.join(import_dir, f"{token}.warnings.csv")

    meta_map = {}
    safe_normals = []
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_file = json.load(f)
            if isinstance(meta_file, dict) and ("meta" in meta_file or "safe" in meta_file):
                meta_map = meta_file.get("meta") or {}
                safe_normals = meta_file.get("safe") or []
            elif isinstance(meta_file, dict):
                meta_map = meta_file
        except Exception:
            meta_map = {}

    existing_title_lookup = {}
    existing_title_values = list(existing_norm_map.values())
    if existing_title_values:
        for bt in BookTitle.query.filter(BookTitle.title.in_(existing_title_values)).all():
            existing_title_lookup[_normalize_title_for_compare(bt.title)] = bt

    for key, entry in aggregates.items():
        cab_name, normalized = key
        warnings = []
        similar_candidates = []
        if row_counts[key] > 1:
            warnings.append("CSV 內重複列，將合併")
        if len(title_variants[key]) > 1:
            warnings.append("書名格式不一致")
        if cab_name not in existing_cabinets:
            warnings.append("新櫃位（將新增）")
        if normalized in existing_norm_map:
            existing_titles_count += 1
            existing_title = existing_norm_map[normalized]
            if existing_title != entry["title"]:
                warnings.append(f"可能重複書名：{existing_title}")
                similar_candidates = [existing_title]
            is_new_title = False
        else:
            new_titles += 1
            is_new_title = True
            if existing_norms:
                import difflib

                matches = difflib.get_close_matches(normalized, existing_norms, n=2, cutoff=0.86)
                if matches:
                    similar_candidates = [existing_norm_map[m] for m in matches]
                    suggested = ", ".join(similar_candidates)
                    warnings.append(f"相似書名：{suggested}")

        existing_meta = meta_map.get(normalized, {})
        existing_title_obj = existing_title_lookup.get(normalized)
        author_value = entry["author"] or existing_meta.get("author") or (existing_title_obj.author if existing_title_obj else "")
        cover_value = normalize_cover_url(
            existing_meta.get("cover_url") or (existing_title_obj.cover_link if existing_title_obj else "")
        )

        preview_rows.append({
            "cabinet": cab_name,
            "title": entry["title"],
            "normalized": normalized,
            "key": f"{cab_name}||{normalized}",
            "variants": sorted(title_variants[key]) if len(title_variants[key]) > 1 else [],
            "similar_titles": similar_candidates,
            "is_new_cabinet": cab_name not in existing_cabinets,
            "duplicate_rows": row_counts[key] > 1,
            "is_new_title": is_new_title,
            "author": author_value or "",
            "cover_url": cover_value or "",
            "in_stock": entry["qty"] > 0,
            "warnings": warnings,
        })

    preview_rows.sort(key=lambda item: (item["cabinet"], item["title"]))

    warning_rows = [row for row in preview_rows if row["warnings"]]
    warning_count = len(warning_rows)
    warning_items = sum(len(row["warnings"]) for row in warning_rows)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(csv_text)
    safe_normals = [row["normalized"] for row in preview_rows if not row["warnings"]]
    safe_fetch_count = sum(
        1
        for row in preview_rows
        if not row["warnings"] and (not row["author"] or not row["cover_url"])
    )
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"meta": meta_map, "safe": safe_normals}, f, ensure_ascii=False)
    except Exception:
        pass

    if warning_rows:
        try:
            with open(warnings_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Cabinet", "Title", "Normalized", "Warning"])
                for row in warning_rows:
                    for note in row["warnings"]:
                        writer.writerow(csv_safe_row([row["cabinet"], row["title"], row["normalized"], note]))
        except Exception:
            pass

    session["import_preview"] = {
        "token": token,
        "path": file_path,
        "meta_path": meta_path,
        "warnings_path": warnings_path if warning_rows else "",
        "warning_count": warning_count,
        "warning_items": warning_items,
        "created_at": datetime.utcnow().isoformat(),
    }

    display_limit = 200
    preview_display = preview_rows[:display_limit]

    missing_author_count = sum(1 for row in preview_rows if not row["author"])
    missing_cover_count = sum(1 for row in preview_rows if not row["cover_url"])

    return render_template(
        "admin_import_preview.html",
        title="匯入預覽",
        preview_rows=preview_display,
        warning_rows=warning_rows,
        total_rows=len(preview_rows),
        invalid_rows=len(invalid_rows),
        has_more=len(preview_rows) > display_limit,
        new_titles=new_titles,
        existing_titles_count=existing_titles_count,
        cabinet_create_count=cabinet_create_count,
        warning_count=warning_count,
        warning_items=warning_items,
        existing_cabinets=sorted(existing_cabinets),
        missing_author_count=missing_author_count,
        missing_cover_count=missing_cover_count,
        safe_fetch_count=safe_fetch_count,
        token=token,
        can_commit=len(invalid_rows) == 0,
        show_top_sellers=False,
    )


@admin_bp.route("/admin/import", methods=["POST"])
def admin_import():
    admin_user, response = _require_roles("advance-admin", json_response=True)
    if response:
        return response

    confirm_text = (request.form.get("confirm_text") or "").strip().upper()
    if confirm_text != "IMPORT":
        return jsonify({"success": False, "message": "請輸入 IMPORT 以確認匯入"}), 400
    password = request.form.get("password") or ""
    if not check_password_hash(admin_user.password_hash, password):
        return jsonify({"success": False, "message": "密碼錯誤"}), 400

    token = (request.form.get("token") or "").strip()
    preview = session.get("import_preview") or {}
    if not token or preview.get("token") != token:
        return jsonify({"success": False, "message": "預覽已過期，請重新上傳"}), 400

    warning_count = int(preview.get("warning_count") or 0)
    if warning_count > 0 and request.form.get("ack_warnings") != "on":
        return jsonify({"success": False, "message": "請勾選已確認警告後再匯入"}), 400

    file_path = preview.get("path")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"success": False, "message": "找不到預覽檔案，請重新上傳"}), 400

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            csv_text = f.read()

        excluded_pairs = set()
        excluded_raw = request.form.get("excluded_warnings") or ""
        if excluded_raw:
            try:
                excluded_items = json.loads(excluded_raw)
            except json.JSONDecodeError:
                excluded_items = []
            for item in excluded_items:
                if not isinstance(item, dict):
                    continue
                cab = (item.get("cabinet") or "").strip()
                title = (item.get("title") or "").strip()
                normalized = (item.get("normalized") or "").strip()
                if not normalized and title:
                    normalized = _normalize_title_for_compare(title)
                if cab and normalized:
                    excluded_pairs.add((cab, normalized))

        title_overrides = {}
        cabinet_overrides = {}
        overrides_raw = request.form.get("title_overrides") or ""
        if overrides_raw:
            try:
                title_overrides = json.loads(overrides_raw) or {}
            except json.JSONDecodeError:
                title_overrides = {}
        cabinet_raw = request.form.get("cabinet_overrides") or ""
        if cabinet_raw:
            try:
                cabinet_overrides = json.loads(cabinet_raw) or {}
            except json.JSONDecodeError:
                cabinet_overrides = {}

        if excluded_pairs or title_overrides or cabinet_overrides:
            output = io.StringIO()
            writer = csv.writer(output)
            reader = csv.reader(io.StringIO(csv_text))

            def is_header(row):
                if len(row) < 2:
                    return False
                first = row[0].strip().lstrip("\ufeff").lower()
                second = row[1].strip().lower()
                return first in {"cabinet", "cabinet_name", "櫃位"} and second in {"title", "book", "書名"}

            for row in reader:
                if len(row) < 2:
                    continue
                if is_header(row):
                    writer.writerow(row)
                    continue
                cab_name = (row[0] or "").lstrip("\ufeff").strip()
                title = (row[1] or "").lstrip("\ufeff").strip()
                norm = _normalize_title_for_compare(title)
                key = f"{cab_name}||{norm}"
                if (cab_name, norm) in excluded_pairs:
                    continue
                if key in cabinet_overrides:
                    cab_name = cabinet_overrides[key]
                if key in title_overrides:
                    title = title_overrides[key]
                row[0] = cab_name
                row[1] = title
                writer.writerow(row)
            csv_text = output.getvalue()

        try:
            backup = _create_backup_archive(note="Auto backup before import")
            log_action("import_start", target=admin_user.username, details=f"backup={backup.filename}")
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("automatic backup before import failed")
            return jsonify({"success": False, "message": "自動備份失敗，請稍後再試。"}), 500

        summary = sync_csv_to_db(csv_text=csv_text, force=True, remove_missing=False)
        meta_path = preview.get("meta_path")
        if meta_path and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_file = json.load(f) or {}
                if isinstance(meta_file, dict) and ("meta" in meta_file or "safe" in meta_file):
                    meta_map = meta_file.get("meta") or {}
                elif isinstance(meta_file, dict):
                    meta_map = meta_file
            except Exception:
                meta_map = {}
            if meta_map:
                reader = csv.reader(io.StringIO(csv_text))
                normalized_titles = set()
                raw_titles = set()
                for row in reader:
                    if len(row) < 2:
                        continue
                    if row and row[0].strip().lstrip("\ufeff").lower() in {"cabinet", "cabinet_name", "櫃位"}:
                        continue
                    raw_title = (row[1] or "").lstrip("\ufeff").strip()
                    if raw_title:
                        normalized_titles.add(_normalize_title_for_compare(raw_title))
                        raw_titles.add(raw_title)
                if normalized_titles:
                    titles = BookTitle.query.filter(BookTitle.title.in_(raw_titles)).all()
                    for bt in titles:
                        norm = _normalize_title_for_compare(bt.title)
                        if norm not in normalized_titles:
                            continue
                        meta = meta_map.get(norm)
                        if not meta:
                            continue
                        if not bt.author and meta.get("author"):
                            bt.author = meta["author"]
                        cover_url = normalize_cover_url(meta.get("cover_url"))
                        if not bt.cover_link and cover_url:
                            bt.cover_link = cover_url
                    db.session.commit()
        details = (
            f"rows={summary.get('rows', 0)} "
            f"pairs={summary.get('pairs', 0)} "
            f"created_inventory={summary.get('created_inventory', 0)} "
            f"archived_inventory={summary.get('archived_inventory', 0)} "
            f"excluded={len(excluded_pairs)} "
            f"title_overrides={len(title_overrides)} "
            f"cabinet_overrides={len(cabinet_overrides)}"
        )
        log_action("import_csv", target=admin_user.username, details=details)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("CSV import failed")
        return jsonify({"success": False, "message": "匯入失敗，請稍後再試。"}), 500
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass
        meta_path = preview.get("meta_path")
        if meta_path:
            try:
                os.remove(meta_path)
            except OSError:
                pass
        warnings_path = preview.get("warnings_path")
        if warnings_path:
            try:
                os.remove(warnings_path)
            except OSError:
                pass
        session.pop("import_preview", None)

    return jsonify({
        "success": True,
        "message": "匯入完成",
        "summary": summary,
    })


@admin_bp.route("/admin/import/warnings")
def admin_import_warnings():
    _, response = _require_roles("advance-admin")
    if response:
        return response

    preview = session.get("import_preview") or {}
    warnings_path = preview.get("warnings_path")
    if not warnings_path or not os.path.exists(warnings_path):
        return redirect(url_for("admin.system_page"))

    with open(warnings_path, "r", encoding="utf-8") as f:
        content = f.read()

    return current_app.response_class(
        response=content,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=import_warnings.csv"
        },
    )


@admin_bp.route("/admin/import/metadata", methods=["POST"])
def admin_import_metadata():
    _, response = _require_roles("advance-admin", json_response=True)
    if response:
        return response

    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    preview = session.get("import_preview") or {}
    if not token or preview.get("token") != token:
        return jsonify({"success": False, "message": "預覽已過期，請重新上傳"}), 400

    file_path = preview.get("path")
    meta_path = preview.get("meta_path")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"success": False, "message": "找不到預覽檔案"}), 400
    if not meta_path:
        return jsonify({"success": False, "message": "無法寫入暫存資訊"}), 400

    try:
        from tools.fetch_author import fetch_author_for_title
        from tools.fetch_cover_url import fetch_url_for_title
    except Exception:
        current_app.logger.exception("failed to import metadata fetchers")
        return jsonify({"success": False, "message": "無法啟動抓取器，請稍後再試。"}), 500

    limit = data.get("limit")
    try:
        limit = int(limit) if limit else None
    except (TypeError, ValueError):
        limit = None

    with open(file_path, "r", encoding="utf-8") as f:
        csv_text = f.read()

    reader = csv.reader(io.StringIO(csv_text))
    titles = []
    seen = set()
    for row in reader:
        if len(row) < 2:
            continue
        first = row[0].strip().lstrip("\ufeff").lower()
        second = row[1].strip().lower()
        if first in {"cabinet", "cabinet_name", "櫃位"} and second in {"title", "book", "書名"}:
            continue
        raw_title = (row[1] or "").lstrip("\ufeff").strip()
        if not raw_title:
            continue
        norm = _normalize_title_for_compare(raw_title)
        if norm in seen:
            continue
        seen.add(norm)
        titles.append((norm, raw_title))

    meta_map = {}
    safe_normals = []
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_file = json.load(f) or {}
            if isinstance(meta_file, dict) and ("meta" in meta_file or "safe" in meta_file):
                meta_map = meta_file.get("meta") or {}
                safe_normals = meta_file.get("safe") or []
            elif isinstance(meta_file, dict):
                meta_map = meta_file
        except Exception:
            meta_map = {}

    if data.get("only_safe") and safe_normals:
        safe_set = set(safe_normals)
        titles = [pair for pair in titles if pair[0] in safe_set]

    missing = []
    for norm, raw_title in titles:
        entry = meta_map.get(norm, {})
        if entry.get("author") and normalize_cover_url(entry.get("cover_url")):
            continue
        missing.append((norm, raw_title))

    titles = missing
    total_remaining = len(titles)

    if limit:
        titles = titles[:max(1, limit)]

    updated = 0
    failed = 0
    for norm, raw_title in titles:
        entry = meta_map.get(norm, {})
        author = entry.get("author")
        cover_url = normalize_cover_url(entry.get("cover_url"))
        if not author:
            try:
                author, _ = fetch_author_for_title(raw_title)
            except Exception:
                author = None
        if not cover_url:
            try:
                cover_url, _ = fetch_url_for_title(raw_title)
                cover_url = normalize_cover_url(cover_url)
            except Exception:
                cover_url = None
        if author or cover_url:
            meta_map[norm] = {
                "author": author or entry.get("author") or "",
                "cover_url": normalize_cover_url(cover_url or entry.get("cover_url")),
            }
            updated += 1
        else:
            failed += 1

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"meta": meta_map, "safe": safe_normals}, f, ensure_ascii=False)
    except Exception:
        return jsonify({"success": False, "message": "寫入暫存失敗"}), 500

    items = []
    for norm, _ in titles:
        meta = meta_map.get(norm)
        if not meta:
            continue
        items.append({
            "normalized": norm,
            "author": meta.get("author") or "",
            "cover_url": normalize_cover_url(meta.get("cover_url")),
        })

    return jsonify({
        "success": True,
        "message": "抓取完成",
        "updated": updated,
        "failed": failed,
        "total": len(titles),
        "remaining": total_remaining,
        "items": items,
    })


@admin_bp.route("/admin/events", methods=["GET", "POST"])
def admin_events():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        _, response = _require_roles("manager")
        if response:
            return response
        title = (request.form.get("title") or "").strip()
        date_start_raw = (request.form.get("date_start") or "").strip()
        date_end_raw = (request.form.get("date_end") or "").strip()
        time_text = (request.form.get("time_text") or "").strip()
        description = (request.form.get("description") or "").strip()
        location = (request.form.get("location") or "").strip()
        note = (request.form.get("note") or "").strip()
        is_active = request.form.get("is_active") == "on"
        book_ids = _parse_book_ids(request.form.get("book_ids", ""))
        date_start = datetime.strptime(date_start_raw, "%Y-%m-%d").date() if date_start_raw else None
        date_end = datetime.strptime(date_end_raw, "%Y-%m-%d").date() if date_end_raw else None
        if date_start and not date_end:
            date_end = date_start
        if title and time_text and description:
            evt = EventSchedule(
                title=title,
                date_start=date_start,
                date_end=date_end,
                time_text=time_text,
                description=description or None,
                location=location or None,
                note=note or None,
                is_active=is_active,
            )
            if book_ids:
                evt.books = BookTitle.query.filter(BookTitle.id.in_(book_ids)).all()
            db.session.add(evt)
            log_action("create_event", target=title)
            db.session.commit()
        return redirect(url_for("admin.admin_events"))

    admin_user = _current_admin_user()
    can_manage_events = bool(admin_user and _role_allows(admin_user.role, {"manager"}))
    events = EventSchedule.query.order_by(EventSchedule.display_order.asc(), EventSchedule.updated_at.desc()).all()
    return render_template(
        "admin_events.html",
        title="活動管理",
        events=events,
        can_manage_events=can_manage_events,
        show_top_sellers=False,
    )


@admin_bp.route("/admin/events/<int:event_id>/update", methods=["POST"])
def update_event(event_id):
    _, response = _require_roles("manager")
    if response:
        return response
    event = EventSchedule.query.get_or_404(event_id)
    title = (request.form.get("title") or "").strip()
    date_start_raw = (request.form.get("date_start") or "").strip()
    date_end_raw = (request.form.get("date_end") or "").strip()
    time_text = (request.form.get("time_text") or "").strip()
    description = (request.form.get("description") or "").strip()
    location = (request.form.get("location") or "").strip()
    note = (request.form.get("note") or "").strip()
    is_active = request.form.get("is_active") == "on"
    book_ids = _parse_book_ids(request.form.get("book_ids", ""))
    date_start = datetime.strptime(date_start_raw, "%Y-%m-%d").date() if date_start_raw else None
    date_end = datetime.strptime(date_end_raw, "%Y-%m-%d").date() if date_end_raw else None
    if date_start and not date_end:
        date_end = date_start
    if title and time_text and description:
        event.title = title
        event.date_start = date_start
        event.date_end = date_end
        event.time_text = time_text
        event.description = description
        event.location = location or None
        event.note = note or None
        event.is_active = is_active
        if book_ids:
            event.books = BookTitle.query.filter(BookTitle.id.in_(book_ids)).all()
        else:
            event.books = []
        log_action("update_event", target=event.title)
        db.session.commit()
    return redirect(url_for("admin.admin_events"))


@admin_bp.route("/admin/events/<int:event_id>/delete", methods=["POST"])
def delete_event(event_id):
    _, response = _require_roles("manager")
    if response:
        return response
    event = EventSchedule.query.get_or_404(event_id)
    title = event.title
    db.session.delete(event)
    log_action("delete_event", target=title)
    db.session.commit()
    return redirect(url_for("admin.admin_events"))


@admin_bp.route("/admin/events/reorder", methods=["POST"])
def reorder_events():
    _, response = _require_roles("manager", json_response=True)
    if response:
        return response
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"success": False, "message": "無效的排序資料"}), 400

    events = EventSchedule.query.filter(EventSchedule.id.in_(ids)).all()
    by_id = {evt.id: evt for evt in events}
    for idx, evt_id in enumerate(ids):
        evt = by_id.get(evt_id)
        if evt:
            evt.display_order = idx
    db.session.commit()
    log_action("reorder_events", target="events", details=f"count={len(ids)}")
    db.session.commit()
    return jsonify({"success": True})


@admin_bp.route("/admin/audit/export")
def export_audit_csv():
    _, response = _require_roles("advance-admin")
    if response:
        return response

    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "actor", "action", "target", "details"])
    for log in logs:
        writer.writerow(csv_safe_row([
            log.created_at.isoformat() if log.created_at else "",
            log.actor or "",
            log.action or "",
            log.target or "",
            (log.details or "").replace("\n", " "),
        ]))
    output.seek(0)

    resp = current_app.response_class(
        response=output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=audit_logs.csv"
        }
    )
    return resp
