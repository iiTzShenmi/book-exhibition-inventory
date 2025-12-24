import csv
import io
from datetime import datetime

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from database.models import AuditLog, Book, BookTitle, Cabinet, EventSchedule, BackupArchive, Inventory, db
from app import (
    active_books_query,
    build_grouped_book_entries,
    cabinet_to_dict,
    log_action,
)


admin_bp = Blueprint("admin", __name__)


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

    all_cabinets = Cabinet.query.order_by(Cabinet.name).all()
    cabinets_payload = [cabinet_to_dict(cab) for cab in all_cabinets]
    audit_logs = (
        AuditLog.query.order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )
    recent_backups = BackupArchive.query.order_by(BackupArchive.created_at.desc()).limit(5).all()
    last_backup_ts = recent_backups[0].created_at.isoformat() if recent_backups else None

    return render_template(
        "admin_dashboard.html",
        grouped_books=grouped_books,
        all_cabinets=all_cabinets,
        cabinets_payload=cabinets_payload,
        audit_logs=audit_logs,
        has_search=has_search,
        last_backup_ts=last_backup_ts,
        recent_backups=recent_backups,
        authors=authors,
    )




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

        new_backup = BackupArchive(
            filename=filename,
            csv_content=csv_string,
            note=f"Manual backup: {count} books",
        )
        db.session.add(new_backup)
        log_action("create_db_backup", target="system", details=f"saved {filename} to DB")
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
        if title and time_text and description:
            evt = EventSchedule(
                title=title,
                time_text=time_text,
                description=description or None,
                location=location or None,
                note=note or None,
                is_active=is_active,
            )
            db.session.add(evt)
            db.session.commit()
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
    if title and time_text and description:
        event.title = title
        event.time_text = time_text
        event.description = description
        event.location = location or None
        event.note = note or None
        event.is_active = is_active
        db.session.commit()
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
    db.session.commit()
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
