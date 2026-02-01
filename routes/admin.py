import csv
import io
import json
import os
import re
import secrets
from collections import Counter, defaultdict
from datetime import datetime

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func
from werkzeug.security import check_password_hash

from database.models import AuditLog, Book, BookTitle, Cabinet, EventSchedule, BackupArchive, Inventory, AdminUser, db
from app import (
    active_books_query,
    build_grouped_book_entries,
    cabinet_to_dict,
    log_action,
    collect_replenish_alerts,
    parse_qty,
    sync_csv_to_db,
)


admin_bp = Blueprint("admin", __name__)


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
        writer.writerow([
            inv.cabinet.name if inv.cabinet else "",
            inv.book_title.title if inv.book_title else "",
            "TRUE" if inv.in_stock else "FALSE",
            inv.book_title.author if inv.book_title and inv.book_title.author else "",
            inv.status,
            inv.updated_at.isoformat() if inv.updated_at else "",
        ])
        count += 1

    csv_string = output.getvalue()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.csv"
    backup_note = note or f"Auto backup: {count} books"

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
    replenish_alerts = [alert for alert in alerts if alert.get("type") == "low-stock"]
    total_views = db.session.query(func.coalesce(func.sum(BookTitle.view_count), 0)).scalar() or 0

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
        replenish_alerts=replenish_alerts,
        total_views=total_views,
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


@admin_bp.route("/admin/system")
def system_page():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    admin_user = None
    admin_id = session.get("admin_id")
    if admin_id:
        admin_user = AdminUser.query.get(admin_id)
    can_upload = bool(admin_user and admin_user.role in {"advance-admin"})

    logs = (
        AuditLog.query.order_by(AuditLog.created_at.desc())
        .limit(200)
        .all()
    )
    recent_backups = BackupArchive.query.order_by(BackupArchive.created_at.desc()).limit(5).all()

    return render_template(
        "admin_system.html",
        title="系統",
        audit_logs=logs,
        recent_backups=recent_backups,
        can_upload=can_upload,
        show_top_sellers=False,
    )


@admin_bp.route("/admin/backups")
def backup_page():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))
    return redirect(url_for("admin.system_page"))




@admin_bp.route("/admin/audit")
def audit_page():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

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
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401
    try:
        new_backup = _create_backup_archive(note="Manual backup")
        log_action("create_db_backup", target="system", details=f"saved {new_backup.filename} to DB")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"備份失敗: {str(e)}"}), 500
    return jsonify({
        "success": True,
        "message": "備份已成功儲存至資料庫",
        "backup": new_backup.to_dict(),
        "timestamp": new_backup.created_at.isoformat() if new_backup.created_at else "",
    })


@admin_bp.route("/admin/import/preview", methods=["POST"])
def admin_import_preview():
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    admin_user = None
    admin_id = session.get("admin_id")
    if admin_id:
        admin_user = AdminUser.query.get(admin_id)
    if not admin_user:
        return redirect(url_for("admin.system_page"))
    if admin_user.role not in {"advance-admin"}:
        return redirect(url_for("admin.system_page"))

    upload = request.files.get("csv_file")
    if not upload or not upload.filename:
        return redirect(url_for("admin.system_page"))
    if not upload.filename.lower().endswith(".csv"):
        return redirect(url_for("admin.system_page"))

    try:
        csv_text = upload.read().decode("utf-8-sig")
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

    import_dir = os.path.join(current_app.root_path, "database", "imports")
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
        cover_value = existing_meta.get("cover_url") or (existing_title_obj.cover_link if existing_title_obj else "")

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
                        writer.writerow([row["cabinet"], row["title"], row["normalized"], note])
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
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    admin_user = None
    admin_id = session.get("admin_id")
    if admin_id:
        admin_user = AdminUser.query.get(admin_id)
    if not admin_user:
        return jsonify({"success": False, "message": "無法確認帳號"}), 403
    if admin_user.role not in {"advance-admin"}:
        return jsonify({"success": False, "message": "權限不足"}), 403

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
        except Exception as exc:
            db.session.rollback()
            return jsonify({"success": False, "message": f"自動備份失敗: {exc}"}), 500

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
                        if not bt.cover_link and meta.get("cover_url"):
                            bt.cover_link = meta["cover_url"]
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
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"匯入失敗: {str(e)}"}), 500
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
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    admin_user = None
    admin_id = session.get("admin_id")
    if admin_id:
        admin_user = AdminUser.query.get(admin_id)
    if not admin_user or admin_user.role not in {"advance-admin"}:
        return redirect(url_for("admin.system_page"))

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
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    admin_user = None
    admin_id = session.get("admin_id")
    if admin_id:
        admin_user = AdminUser.query.get(admin_id)
    if not admin_user:
        return jsonify({"success": False, "message": "無法確認帳號"}), 403
    if admin_user.role not in {"advance-admin"}:
        return jsonify({"success": False, "message": "權限不足"}), 403

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
    except Exception as exc:
        return jsonify({"success": False, "message": f"無法啟動抓取器: {exc}"}), 500

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
        if entry.get("author") and entry.get("cover_url"):
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
        cover_url = entry.get("cover_url")
        if not author:
            try:
                author, _ = fetch_author_for_title(raw_title)
            except Exception:
                author = None
        if not cover_url:
            try:
                cover_url, _ = fetch_url_for_title(raw_title)
            except Exception:
                cover_url = None
        if author or cover_url:
            meta_map[norm] = {
                "author": author or entry.get("author") or "",
                "cover_url": cover_url or entry.get("cover_url") or "",
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
            "cover_url": meta.get("cover_url") or "",
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
        title = (request.form.get("title") or "").strip()
        time_text = (request.form.get("time_text") or "").strip()
        description = (request.form.get("description") or "").strip()
        location = (request.form.get("location") or "").strip()
        note = (request.form.get("note") or "").strip()
        is_active = request.form.get("is_active") == "on"
        book_ids = _parse_book_ids(request.form.get("book_ids", ""))
        if title and time_text and description:
            evt = EventSchedule(
                title=title,
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

    events = EventSchedule.query.order_by(EventSchedule.display_order.asc(), EventSchedule.updated_at.desc()).all()
    return render_template(
        "admin_events.html",
        title="活動管理",
        events=events,
        show_top_sellers=False,
    )


@admin_bp.route("/admin/events/<int:event_id>/update", methods=["POST"])
def update_event(event_id):
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))
    event = EventSchedule.query.get_or_404(event_id)
    title = (request.form.get("title") or "").strip()
    time_text = (request.form.get("time_text") or "").strip()
    description = (request.form.get("description") or "").strip()
    location = (request.form.get("location") or "").strip()
    note = (request.form.get("note") or "").strip()
    is_active = request.form.get("is_active") == "on"
    book_ids = _parse_book_ids(request.form.get("book_ids", ""))
    if title and time_text and description:
        event.title = title
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
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))
    event = EventSchedule.query.get_or_404(event_id)
    title = event.title
    db.session.delete(event)
    log_action("delete_event", target=title)
    db.session.commit()
    return redirect(url_for("admin.admin_events"))


@admin_bp.route("/admin/events/reorder", methods=["POST"])
def reorder_events():
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401
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
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

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

    resp = current_app.response_class(
        response=output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=audit_logs.csv"
        }
    )
    return resp
