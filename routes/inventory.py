import re
from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from database.models import Book, BookTitle, Cabinet, EventSchedule, db
from app import (
    active_books_query,
    book_to_dict,
    build_grouped_book_entries,
    cabinet_to_dict,
    cabinet_type_name,
    get_or_create_title,
    get_top_sellers,
    is_postgres,
    log_action,
)


inventory_bp = Blueprint("inventory", __name__)


@inventory_bp.route("/")
def home():
    top_sellers = get_top_sellers(limit=10)
    random_picks = active_books_query().order_by(func.random()).limit(10).all()
    random_picks_data = [book_to_dict(book) for book in random_picks]
    events = (
        EventSchedule.query
        .filter_by(is_active=True)
        .order_by(EventSchedule.display_order.asc(), EventSchedule.updated_at.desc())
        .all()
    )
    return render_template(
        "home.html",
        title="書展庫存系統",
        show_top_sellers=True,
        top_sellers=top_sellers,
        random_picks=random_picks_data,
        events=events,
    )


@inventory_bp.route("/toggle/<int:book_id>", methods=["POST"])
def toggle_stock(book_id):
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    try:
        book = active_books_query().filter_by(id=book_id).first_or_404()
        title = book.title
        book.status = "archived"
        book.deleted_at = datetime.utcnow()
        book.in_stock = False
        log_action("toggle_stock", target=title, details="archived (quantity tracking removed)")
        db.session.commit()
    except Exception:
        db.session.rollback()
    return redirect(url_for("admin.dashboard"))


@inventory_bp.route("/modify_cabinet/<string:title>", methods=["POST"])
def modify_cabinet(title):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    action = request.form.get("add_or_remove")
    cab_name = request.form.get("cabinet", "").strip()
    if not cab_name:
        return jsonify({"success": False, "message": "請輸入櫃位名稱"})

    cabinet = Cabinet.query.filter_by(name=cab_name).first()
    if not cabinet:
        simplified = re.sub(r"\s+(true|false)$", "", cab_name, flags=re.IGNORECASE).strip()
        if simplified and simplified != cab_name:
            cabinet = Cabinet.query.filter_by(name=simplified).first()
            if cabinet:
                cab_name = simplified
    if not cabinet and action == "add":
        try:
            cabinet = Cabinet(name=cab_name)
            db.session.add(cabinet)
            log_action("create_cabinet_from_book", target=cab_name, details=f"title={title}")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500

    if not cabinet:
        return jsonify({"success": False, "message": f"櫃位「{cab_name}」不存在"})

    title_obj = get_or_create_title(title)
    if not title_obj or not title_obj.id:
        return jsonify({"success": False, "message": "無法建立或取得書名"})

    if action == "add":
        existing = active_books_query().filter_by(title_id=title_obj.id, cabinet_id=cabinet.id).first()
        book_id = None
        if existing:
            book_id = existing.id
        else:
            if not title_obj.id or not cabinet.id:
                return jsonify({"success": False, "message": "無效的書名ID或櫃位ID"})
            new_book = Book(title_id=title_obj.id, cabinet_id=cabinet.id)
            db.session.add(new_book)
            db.session.flush()
            book_id = new_book.id
        log_action("add_cabinet_to_title", target=title, details=f"cabinet={cab_name}")
        db.session.commit()
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

    elif action == "remove":
        book = active_books_query().filter_by(title_id=title_obj.id, cabinet_id=cabinet.id).first()
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

        try:
            book.status = "archived"
            book.deleted_at = datetime.utcnow()
            book.in_stock = False
            log_action("remove_cabinet_from_title", target=title, details=f"cabinet={cab_name},archived=true")
            db.session.commit()
            return jsonify({
                "success": True,
                "message": f"已將《{title}》 從 {cab_name} 移除",
                "action": "remove",
                "cabinet_id": cabinet.id,
                "cabinet_name": cab_name,
                "title": title,
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500


@inventory_bp.route("/cabinets", methods=["GET"])
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


@inventory_bp.route("/cabinets", methods=["POST"])
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
    log_action("create_cabinet", target=name, details=f"type={cab_type}")
    db.session.commit()
    return jsonify({
        "success": True,
        "cabinet": cabinet_to_dict(cabinet),
        "affected_titles": [],
    }), 201


@inventory_bp.route("/cabinets/<int:cabinet_id>", methods=["PATCH"])
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

    try:
        log_action(
            "update_cabinet",
            target=cabinet.name,
            details=f"name={cabinet.name},type={cabinet.type}",
        )
        db.session.commit()
        affected_titles = sorted({book.title for book in cabinet.books if getattr(book, "status", "active") == "active"})
        return jsonify({
            "success": True,
            "cabinet": cabinet_to_dict(cabinet),
            "affected_titles": affected_titles,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500


@inventory_bp.route("/cabinets/<int:cabinet_id>", methods=["DELETE"])
def delete_cabinet(cabinet_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    cabinet = Cabinet.query.get_or_404(cabinet_id)
    active_books = [b for b in cabinet.books if getattr(b, "status", "active") == "active"]
    if active_books:
        return jsonify({"success": False, "message": "櫃位仍有書籍，無法刪除"}), 400

    try:
        deleted_payload = {"name": cabinet.name, "type": cabinet.type}
        db.session.delete(cabinet)
        log_action("delete_cabinet", target=cabinet.name)
        db.session.commit()
        return jsonify({"success": True, "cabinet_id": cabinet_id, "deleted": deleted_payload})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500


@inventory_bp.route("/cabinets/<int:cabinet_id>/books", methods=["GET"])
def list_cabinet_books(cabinet_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    cabinet = Cabinet.query.get_or_404(cabinet_id)
    books = (
        active_books_query()
        .filter_by(cabinet_id=cabinet.id)
        .options(joinedload(Book.book_title))
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


@inventory_bp.route("/cabinets/<int:cabinet_id>/books/<int:book_id>/toggle", methods=["PATCH"])
def toggle_cabinet_book(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    book = (
        (active_books_query().with_for_update() if is_postgres() else active_books_query())
        .filter_by(id=book_id, cabinet_id=cabinet_id)
        .first_or_404()
    )
    try:
        title = book.title
        book.in_stock = not book.in_stock
        log_action("toggle_cabinet_book", target=title, details=f"cabinet_id={cabinet_id},in_stock={book.in_stock}")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500
    return jsonify(
        {
            "success": True,
            "book": book_to_dict(book),
            "affected_titles": [book.title],
            "in_stock": book.in_stock,
        }
    )


@inventory_bp.route("/cabinets/<int:cabinet_id>/books/<int:book_id>/adjust", methods=["PATCH"])
def adjust_cabinet_book_quantity(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    payload = request.get_json(silent=True) or {}
    try:
        delta = int(payload.get("delta", 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "delta 必須為數字"}), 400

    book = (
        active_books_query()
        .filter_by(id=book_id, cabinet_id=cabinet_id)
        .first_or_404()
    )

    try:
        if delta < 0:
            title = book.title
            book.status = "archived"
            book.deleted_at = datetime.utcnow()
            book.in_stock = False
            log_action("adjust_quantity_delete", target=title, details=f"cabinet_id={cabinet_id},archived=true")
            db.session.commit()
            return jsonify({"success": True, "book_id": book_id, "affected_titles": [title]})

        book.updated_at = datetime.utcnow()
        log_action("adjust_quantity", target=book.title, details=f"cabinet_id={cabinet_id},delta={delta} (quantity tracking removed)")
        db.session.commit()
        return jsonify({"success": True, "book": book_to_dict(book), "affected_titles": [book.title]})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500


@inventory_bp.route("/add_book", methods=["POST"])
def add_book():
    title = request.form.get("title", "").strip()
    cabinet_id = request.form.get("cabinet_id", type=int)
    amount = request.form.get("amount", type=int, default=1)

    if not title or not cabinet_id:
        return jsonify({"success": False, "message": "缺少書名或櫃位"}), 400

    cabinet = Cabinet.query.get(cabinet_id)
    if not cabinet:
        return jsonify({"success": False, "message": "櫃位不存在"}), 400

    title_obj = get_or_create_title(title)
    if not title_obj or not title_obj.id:
        return jsonify({"success": False, "message": "無法建立或取得書名"}), 400

    existing = active_books_query().filter_by(title_id=title_obj.id, cabinet_id=cabinet_id).first()

    try:
        if existing:
            log_action("restock_book", target=title_obj.title, details=f"cabinet_id={cabinet_id} (quantity tracking removed)")
            db.session.commit()
            return jsonify({
                "success": True,
                "message": "已補貨",
                "book_id": existing.id,
                "cabinet_id": cabinet_id,
                "title": title_obj.title,
                "amount_added": 1,
                "created": False,
            }), 200
        else:
            if not title_obj.id or not cabinet_id:
                return jsonify({"success": False, "message": "無效的書名ID或櫃位ID"}), 400
            new_book = Book(
                title_id=title_obj.id,
                cabinet_id=cabinet_id,
            )
            db.session.add(new_book)
            log_action("add_book", target=title_obj.title, details=f"cabinet_id={cabinet_id},amount={amount}")
            db.session.commit()
            return jsonify({
                "success": True,
                "message": "書籍已新增",
                "book_id": new_book.id,
                "cabinet_id": cabinet_id,
                "title": title_obj.title,
                "amount_added": 1,
                "created": True,
            }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500


@inventory_bp.route("/cabinets/<int:cabinet_id>/books/<int:book_id>/move", methods=["PATCH"])
def move_cabinet_book(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    book = (
        active_books_query()
        .filter_by(id=book_id, cabinet_id=cabinet_id)
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

    try:
        duplicate_query = active_books_query()
        if is_postgres():
            duplicate_query = duplicate_query.with_for_update()
        duplicate = duplicate_query.filter_by(title_id=book.title_id, cabinet_id=target.id).first()
        if duplicate:
            book.status = "archived"
            book.deleted_at = datetime.utcnow()
            book.in_stock = False
            book = duplicate
        else:
            book.cabinet_id = target.id
        log_action("move_book", target=book.title, details=f"{cabinet_id} -> {target.id}")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "該書已存在於目標櫃位，請重新整理後再試"}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500
    return jsonify(
        {
            "success": True,
            "book": book_to_dict(book),
            "source_cabinet_id": cabinet_id,
            "target_cabinet_id": target.id,
            "affected_titles": [book.title],
        }
    )


@inventory_bp.route("/replenish/<string:title>", methods=["POST"])
def replenish_from_reserve(title):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401
    
    payload = request.get_json(silent=True) or {}
    display_cabinet_id = payload.get("display_cabinet_id")
    reserve_cabinet_id = payload.get("reserve_cabinet_id")
    reserve_book_id = payload.get("reserve_book_id")
    
    if not display_cabinet_id or not reserve_cabinet_id or not reserve_book_id:
        return jsonify({"success": False, "message": "缺少必要參數"}), 400
    
    try:
        reserve_query = active_books_query().with_for_update() if is_postgres() else active_books_query()
        reserve_book = reserve_query.filter_by(
            id=reserve_book_id,
            cabinet_id=reserve_cabinet_id
        ).first_or_404()
        
        display_cabinet = Cabinet.query.get_or_404(display_cabinet_id)
        if (display_cabinet.type or "").strip().lower() != "display":
            return jsonify({"success": False, "message": "目標櫃位必須是展示櫃"}), 400
        
        title_obj = reserve_book.book_title
        existing_query = active_books_query().with_for_update() if is_postgres() else active_books_query()
        existing = existing_query.filter_by(
            title_id=title_obj.id,
            cabinet_id=display_cabinet_id
        ).first()
        
        if existing:
            if not existing.in_stock:
                existing.in_stock = True
                log_action("replenish_book", target=title_obj.title, 
                          details=f"toggled in_stock for existing book in display cabinet {display_cabinet.name}")
            else:
                return jsonify({
                    "success": False,
                    "message": f"《{title_obj.title}》已在展示櫃「{display_cabinet.name}」中"
                }), 400
        else:
            old_cabinet_name = reserve_book.cabinet.name if reserve_book.cabinet else ""
            reserve_book.cabinet_id = display_cabinet_id
            reserve_book.in_stock = True
            log_action("replenish_book", target=title_obj.title,
                      details=f"moved from reserve cabinet {old_cabinet_name} to display cabinet {display_cabinet.name}")
        
        db.session.commit()
        return jsonify({
            "success": True,
            "message": f"已從備書櫃補貨至「{display_cabinet.name}」",
            "affected_titles": [title_obj.title],
            "book": book_to_dict(existing if existing else reserve_book),
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500


@inventory_bp.route("/cabinets/<int:cabinet_id>/books/<int:book_id>", methods=["DELETE"])
def remove_cabinet_book(cabinet_id, book_id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    book = (
        active_books_query()
        .filter_by(id=book_id, cabinet_id=cabinet_id)
        .first_or_404()
    )
    try:
        title = book.title
        book.status = "archived"
        book.deleted_at = datetime.utcnow()
        book.in_stock = False
        log_action("remove_book_from_cabinet", target=title, details=f"cabinet_id={cabinet_id},archived=true")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500
    return jsonify(
        {
            "success": True,
            "book_id": book_id,
            "affected_titles": [title],
            "title": title,
            "cabinet_id": cabinet_id,
        }
    )
