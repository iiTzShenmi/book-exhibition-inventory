from collections import defaultdict
from datetime import datetime
import time

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, render_template_string, request, send_from_directory, session, url_for
from sqlalchemy import func, or_

from database.models import Book, BookTitle, Cabinet, EventSchedule, db
from similarity import BookProfile, suggest_for_missing_title, parse_topics_field
from app import (
    active_books_query,
    build_grouped_book_entries,
    cabinet_to_dict,
    collect_replenish_alerts,
    cover_url_for_title,
    get_csrf_token,
    log_action,
    is_postgres,
)


api_bp = Blueprint("api", __name__)


@api_bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("inventory.home"))

    MAX_SEARCH_RESULTS = 200

    base_query = active_books_query().join(BookTitle)
    if is_postgres():
        ts_query = func.plainto_tsquery("simple", query.replace(" ", " & "))
        title_vector = func.to_tsvector("simple", BookTitle.title)
        topic_vector = func.to_tsvector("simple", func.coalesce(BookTitle.topics, ""))
        ilike_filter = BookTitle.title.ilike(f"%{query}%")
        base_query = base_query.filter(
            title_vector.op("@@")(ts_query) | topic_vector.op("@@")(ts_query) | ilike_filter
        )
    else:
        base_query = base_query.filter(BookTitle.title.contains(query))
    base_query = base_query.order_by(Book.updated_at.desc())
    results = base_query.limit(MAX_SEARCH_RESULTS).all()

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
    has_exact_results = len(results) > 0
    try:
        query_norm = query.strip().lower()
        if len(query_norm) >= 2:
            query_chars = list(query_norm)
            if len(query_chars) >= 2:
                char_filters = []
                for char in query_chars[:5]:
                    char_filters.append(BookTitle.title.ilike(f"%{char}%"))

                exact_filter = BookTitle.title.ilike(f"%{query_norm}%")
                topic_filter = BookTitle.topics.ilike(f"%{query_norm}%")
                char_filter = or_(*char_filters) if char_filters else None

                if char_filter is not None:
                    filtered_books = (
                        active_books_query()
                        .join(BookTitle)
                        .filter(exact_filter | topic_filter | char_filter)
                        .order_by(Book.updated_at.desc())
                        .limit(100)
                        .all()
                    )
                else:
                    filtered_books = (
                        active_books_query()
                        .join(BookTitle)
                        .filter(exact_filter | topic_filter)
                        .order_by(Book.updated_at.desc())
                        .limit(50)
                        .all()
                    )
            else:
                exact_filter = BookTitle.title.ilike(f"%{query_norm}%")
                topic_filter = BookTitle.topics.ilike(f"%{query_norm}%")
                filtered_books = (
                    active_books_query()
                    .join(BookTitle)
                    .filter(exact_filter | topic_filter)
                    .order_by(Book.updated_at.desc())
                    .limit(50)
                    .all()
                )
        else:
            limit = 100 if not has_exact_results else 30
            filtered_books = (
                active_books_query()
                .join(BookTitle)
                .order_by(Book.updated_at.desc())
                .limit(limit)
                .all()
            )

        profiles = []
        books_by_title = defaultdict(list)
        for b in filtered_books:
            bt = b.book_title if hasattr(b, "book_title") else None
            cab = b.cabinet if hasattr(b, "cabinet") else None
            book_title = b.title if hasattr(b, "title") else (bt.title if bt else "")

            if not book_title or not book_title.strip():
                continue
            profiles.append(
                BookProfile(
                    title=book_title,
                    author=bt.author if bt else "",
                    cabinet=cab.name if cab else "",
                    cabinet_type=(cab.type if cab else "") or "",
                    topics=parse_topics_field(getattr(bt, "topics", None) if bt else None),
                    in_stock=b.in_stock,
                )
            )
            books_by_title[book_title].append(b)

        if profiles:
            top_count = 10 if not has_exact_results else 5
            scored_suggestions = suggest_for_missing_title(profiles, query, top=top_count)

            if scored_suggestions:
                result_titles = {book.title for book in results} if results else set()
                suggestion_count = 0
                for score, prof in scored_suggestions:
                    if prof.title in result_titles:
                        continue

                    entries_map = build_grouped_book_entries(
                        books_by_title.get(prof.title, []),
                        include_reserve=True,
                        include_reserve_out_of_stock=True,
                        show_counts=False,
                    )

                    suggestion_cover = None
                    suggestion_books = books_by_title.get(prof.title, [])
                    if suggestion_books:
                        title_obj = getattr(suggestion_books[0], "book_title", None)
                        if title_obj and title_obj.cover_link:
                            suggestion_cover = title_obj.cover_link

                    suggestions.append(
                        {
                            "title": prof.title,
                            "topics": prof.topics or [],
                            "entries": entries_map.get(prof.title, []),
                            "score": round(score, 3),
                            "author": prof.author or "",
                            "cover": suggestion_cover,
                        }
                    )
                    suggestion_count += 1
                    if suggestion_count >= 5:
                        break
    except Exception as e:
        print(f"[similarity] Error generating suggestions: {e}")
        import traceback
        traceback.print_exc()

    all_cabinets = Cabinet.query.order_by(Cabinet.name.asc()).all()
    all_cabinets_data = [cabinet_to_dict(cabinet) for cabinet in all_cabinets]

    return render_template(
        "search_results.html",
        grouped_books=grouped_books,
        query=query,
        show_top_sellers=False,
        covers=covers,
        authors=authors,
        suggestions=suggestions,
        all_cabinets=all_cabinets_data,
    )


@api_bp.route("/book_details/<string:title>")
def book_details(title):
    books = active_books_query().join(BookTitle).filter(BookTitle.title == title).all()
    if not books:
        return jsonify({"error": "Book not found"}), 404

    _increment_view_count(title)

    is_admin = bool(session.get("is_admin"))
    grouped_map = build_grouped_book_entries(
        books,
        include_id=is_admin,
        reference_books=books,
        include_reserve=is_admin,
        include_reserve_out_of_stock=is_admin,
        include_cabinet_id=is_admin,
    )
    entries = grouped_map.get(title, [])

    if not is_admin:
        modal_html = render_template_string("""
        <div class="modal-content">
        <h2>{{ title }}</h2>
        {% for entry in entries %}
          <div class="modal-row">
            <span>{{ entry.cabinet }}</span>
            <span class="stat {{ entry.cls }}">{{ entry.status }}</span>
          </div>
        {% endfor %}
        <button class="close-btn" onclick="closeModal()">關閉</button>
        </div>
        """, title=title, entries=entries)

        response = make_response(modal_html)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    modal_html = render_template_string("""
        <div class="modal-content">
        <h2>{{ title }}</h2>
        {% for entry in entries %}
            <div class="modal-row">
            <span>{{ entry.cabinet }}</span>
            <form action="{{ url_for('api.toggle_modal_stock', id=entry.id) }}" method="post" class="inline-form" data-skip-confirm="true">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button type="submit" class="toggle-btn stat {{ entry.cls }}">
                {{ entry.status }}
                </button>
            </form>
            </div>
            {% if entry.notes %}
            {% for note in entry.notes %}
            <div class="reserve-hint{% if entry.replenish %} replenish-hint{% endif %}" 
                 {% if entry.replenish %}
                 data-title="{{ title }}"
                 data-display-cabinet-id="{{ entry.replenish.display_cabinet_id }}"
                 data-reserve-cabinet-id="{{ entry.replenish.reserve_cabinet_id }}"
                 data-reserve-book-id="{{ entry.replenish.reserve_book_id }}"
                 data-reserve-cabinet-name="{{ entry.replenish.reserve_cabinet_name }}"
                 {% endif %}>
                {% if not entry.replenish %}
                {{ note }}
                {% endif %}
                {% if entry.replenish %}
                <div>📦 請從「{{ entry.replenish.reserve_cabinet_name }}」補貨</div>
                {% endif %}
            </div>
            {% endfor %}
            {% endif %}
        {% endfor %}
        <button class="close-btn" onclick="closeModal()">關閉</button>
        </div>
        """, title=title, entries=entries, csrf_token=get_csrf_token())

    response = make_response(modal_html)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _increment_view_count(title: str, debounce_seconds: int = 10):
    title_obj = BookTitle.query.filter_by(title=title).first()
    if not title_obj:
        return
    now_ts = time.time()
    viewed = session.get("view_debounce", {})
    last_ts = viewed.get(title)
    if last_ts and (now_ts - last_ts) <= debounce_seconds:
        return
    viewed[title] = now_ts
    session["view_debounce"] = viewed
    session.modified = True
    try:
        db.session.query(BookTitle).filter_by(id=title_obj.id).update({
            BookTitle.view_count: BookTitle.view_count + 1,
            BookTitle.last_viewed_at: datetime.utcnow(),
        })
        db.session.commit()
    except Exception:
        db.session.rollback()


@api_bp.route("/api/track_view", methods=["POST", "GET"])
def track_view():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        title = (request.form.get("title") or "").strip()
    if not title:
        title = (request.args.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "message": "title required"}), 400
    title = " ".join(title.split())
    _increment_view_count(title)
    return jsonify({"success": True})


@api_bp.route("/api/title_cabinets/<string:title>")
def title_cabinets(title):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "未登入"}), 401

    title_obj = BookTitle.query.filter_by(title=title).first()
    if not title_obj:
        return jsonify({"success": False, "message": "書名不存在"}), 404

    books = active_books_query().filter_by(title_id=title_obj.id).join(Cabinet).all()
    payload = []
    for book in books:
        cab = book.cabinet
        payload.append({
            "cabinet": cab.name if cab else "",
            "type": cab.type if cab else "",
            "in_stock": book.in_stock,
        })
    return jsonify({"success": True, "title": title, "cabinets": payload})


@api_bp.route("/book_card/<string:title>")
def book_card(title):
    if not session.get("is_admin"):
        return redirect(url_for("auth.login"))

    books = active_books_query().join(BookTitle).filter(BookTitle.title == title).all()
    if not books:
        return "<div class='card'>未找到此書</div>"

    title_obj = BookTitle.query.filter_by(title=title).first()
    reference_books = active_books_query().filter_by(title_id=title_obj.id).all() if title_obj else books

    grouped_map = build_grouped_book_entries(
        books,
        include_id=True,
        include_reserve=False,
        reference_books=reference_books,
    )
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
            <div class="reserve-hint">
                <span>{{ note }}</span>
                {% if entry.replenish %}
                <div class="reserve-meta">
                  {{ entry.replenish.reserve_cabinet_name }} 備書可取
                </div>
                {% endif %}
                {% if entry.replenish %}
                <button type="button" class="replenish-btn" 
                        data-title="{{ title }}"
                        data-display-cabinet-id="{{ entry.replenish.display_cabinet_id }}"
                        data-reserve-cabinet-id="{{ entry.replenish.reserve_cabinet_id }}"
                        data-reserve-book-id="{{ entry.replenish.reserve_book_id }}"
                        data-reserve-cabinet-name="{{ entry.replenish.reserve_cabinet_name }}">
                    從「{{ entry.replenish.reserve_cabinet_name }}」補貨
                </button>
                {% endif %}
            </div>
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


@api_bp.route("/api/cabinets")
def list_cabinets():
    cabinets = Cabinet.query.order_by(Cabinet.name.asc()).all()
    payload = [
        {"id": cab.id, "name": cab.name, "type": (cab.type or "").strip().lower()}
        for cab in cabinets
    ]
    return jsonify({"success": True, "cabinets": payload})


@api_bp.route("/api/cabinets/<int:cabinet_id>/featured")
def cabinet_featured(cabinet_id):
    cabinet = Cabinet.query.get_or_404(cabinet_id)
    books = (
        active_books_query()
        .filter_by(cabinet_id=cabinet_id)
        .join(BookTitle)
        .order_by(Book.updated_at.desc())
        .limit(30)
        .all()
    )
    titles = []
    seen = set()
    for book in books:
        title = book.title
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
        if len(titles) >= 8:
            break
    return jsonify({
        "success": True,
        "cabinet": {
            "id": cabinet.id,
            "name": cabinet.name,
            "type": (cabinet.type or "").strip().lower(),
        },
        "titles": titles,
    })


@api_bp.route("/toggle_modal_stock/<int:id>", methods=["POST"])
def toggle_modal_stock(id):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        book = active_books_query().filter_by(id=id).first_or_404()
        title = book.title
        book.in_stock = not book.in_stock
        if book.status != "active":
            book.status = "active"
            book.deleted_at = None
        db.session.commit()
        log_action("toggle_modal_stock", target=title, details=f"in_stock={book.in_stock}")
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"操作失敗: {str(e)}"}), 500

    status_text = "在庫" if book.in_stock else "缺貨"
    return jsonify({
        "success": True,
        "message": f"《{title}》狀態已更新為 {status_text}",
        "title": title,
        "in_stock": book.in_stock,
    })


@api_bp.route("/api/notifications")
def get_notifications():
    if not session.get("is_admin"):
        return jsonify([])

    alerts = collect_replenish_alerts()
    return jsonify(alerts)


@api_bp.route("/api/events")
def get_events():
    events = (
        EventSchedule.query
        .filter_by(is_active=True)
        .order_by(EventSchedule.display_order.asc(), EventSchedule.updated_at.desc())
        .all()
    )
    payload = [
        {
            "id": evt.id,
            "title": evt.title,
            "date_start": evt.date_start.isoformat() if evt.date_start else None,
            "date_end": evt.date_end.isoformat() if evt.date_end else None,
            "time_text": evt.time_text,
            "description": evt.description,
            "location": evt.location,
            "note": evt.note,
            "books": [
                {
                    "id": book.id,
                    "title": book.title,
                    "author": book.author,
                    "cover_url": cover_url_for_title(book),
                }
                for book in (evt.books or [])
            ],
        }
        for evt in events
    ]
    return jsonify({"success": True, "events": payload})


@api_bp.route("/api/book_titles")
def book_titles():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"success": True, "results": []})

    title_filter = BookTitle.title.ilike(f"%{query}%")
    author_filter = BookTitle.author.ilike(f"%{query}%")
    results = (
        BookTitle.query
        .filter(title_filter | author_filter)
        .order_by(BookTitle.updated_at.desc())
        .limit(20)
        .all()
    )
    payload = [
        {
            "id": title.id,
            "title": title.title,
            "author": title.author,
            "cover_url": cover_url_for_title(title),
        }
        for title in results
    ]
    return jsonify({"success": True, "results": payload})


@api_bp.route("/api/realtime_status")
def realtime_status():
    alerts = collect_replenish_alerts()
    messages = [alert.get("message") for alert in alerts if alert.get("message")]
    return jsonify({
        "success": True,
        "has_work": bool(messages),
        "count": len(messages),
        "messages": messages[:10],
    })


@api_bp.route("/titles/<int:title_id>/cover")
def title_cover(title_id):
    title_obj = BookTitle.query.get_or_404(title_id)
    return jsonify({
        "title_id": title_obj.id,
        "title": title_obj.title,
        "cover_url": cover_url_for_title(title_obj),
        "cover_link": title_obj.cover_link,
    })


@api_bp.route("/sw.js")
def service_worker():
    return send_from_directory(current_app.static_folder, "sw.js")
