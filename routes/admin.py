import csv
import io
import json
import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from database.models import AuditLog, Book, BookTitle, Cabinet, db
from app import (
    LAST_BACKUP_META,
    active_books_query,
    build_grouped_book_entries,
    cabinet_to_dict,
    create_backup,
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
        backups = create_backup()
        with open(LAST_BACKUP_META, "w", encoding="utf-8") as f:
            json.dump({"last": datetime.utcnow().isoformat()}, f)
        log_action(
            "create_backup",
            target="system",
            details=f"db={os.path.basename(backups.get('db', '') or '')},csv={os.path.basename(backups.get('csv', '') or '')}",
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"備份失敗: {str(e)}"}), 500
    return jsonify({"success": True, "message": "備份完成", "backups": backups, "timestamp": backups["timestamp"]})


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
