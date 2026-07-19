import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

import app as app_module
import pytest
from app import (
    COVER_PLACEHOLDER_URL,
    cover_url_for_title,
    csv_safe_cell,
    is_allowed_cover_url,
    normalize_cover_url,
    pg_env_from_database_url,
)
from database.models import (
    AdminInvite,
    AdminUser,
    AuditLog,
    BookTitle,
    Cabinet,
    Inventory,
    IssueReport,
    db,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash


def test_security_headers_are_present(client):
    response = client.get("/")

    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src-attr 'none'" in csp
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_public_header_displays_configured_release_version(client):
    response = client.get("/")

    assert response.status_code == 200
    assert f"EXIS v{app_module.APP_VERSION}" in response.get_data(as_text=True)


def test_project_documentation_displays_configured_release_version(client):
    response = client.get("/")

    assert response.status_code == 200
    assert f"<strong>目前版本：</strong> v{app_module.APP_VERSION}" in response.get_data(as_text=True)


def test_disabled_quick_guide_control_stays_hidden_on_mobile():
    template = Path("templates/base.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/exis_refresh.css").read_text(encoding="utf-8")

    assert 'class="hero-brand__action u-hidden"' in template
    assert ".hero-brand__action.u-hidden" in stylesheet


def test_login_requires_csrf(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong"})

    assert response.status_code == 400


def test_login_uses_external_stylesheet_under_strict_csp(client):
    response = client.get("/login")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "css/login.css" in body
    assert "<style>" not in body
    assert "style-src 'self'" in response.headers["Content-Security-Policy"]

    stylesheet = client.get("/static/css/login.css")
    assert stylesheet.status_code == 200
    assert b"body.login-page" in stylesheet.data


def test_system_audit_log_starts_collapsed_until_expanded(csrf_client):
    user = AdminUser(
        username="system-admin",
        email="system-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="advance-admin",
    )
    db.session.add(user)
    db.session.commit()
    with csrf_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = user.username
        sess["admin_id"] = user.id
        sess["admin_role"] = user.role

    response = csrf_client.get("/admin/system")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="card admin-audit-card is-collapsed"' in body
    assert 'id="audit-toggle"' in body
    assert 'aria-expanded="false"' in body
    assert 'id="audit-body" aria-hidden="true"' in body

    script = Path("static/js/admin_system.js").read_text(encoding="utf-8")
    assert "const setExpanded" in script
    assert "toggle.setAttribute('aria-expanded'" in script


def test_csrf_rejects_client_seeded_token(client):
    response = client.post(
        "/api/report_issue",
        json={"name": "tester", "type": "bug", "description": "client seeded token"},
        headers={"X-CSRF-Token": "attacker-chosen-token"},
    )

    assert response.status_code == 400


def test_login_rejects_first_post_with_attacker_token(client):
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "whatever",
            "csrf_token": "attacker-chosen-token",
        },
    )

    assert response.status_code == 400


def test_logout_is_post_only(client):
    response = client.get("/logout")

    assert response.status_code == 405


def test_view_tracking_requires_post_and_csrf(csrf_client):
    title = BookTitle(title="Tracked view", view_count=0)
    db.session.add(title)
    db.session.commit()

    get_response = csrf_client.get("/api/track_view?title=Tracked%20view")
    assert get_response.status_code == 405

    post_response = csrf_client.post(
        "/api/track_view",
        json={"title": "Tracked view"},
        headers={"X-CSRF-Token": "test-csrf"},
    )
    assert post_response.status_code == 200
    assert post_response.get_json()["success"] is True
    assert db.session.get(BookTitle, title.id).view_count == 1


def test_book_details_get_does_not_change_view_count(client):
    title = BookTitle(title="Read-only details", view_count=0)
    cabinet = Cabinet(name="Details cabinet", type="display")
    db.session.add_all([title, cabinet])
    db.session.flush()
    db.session.add(Inventory(title_id=title.id, cabinet_id=cabinet.id, in_stock=True))
    db.session.commit()

    response = client.get("/book_details/Read-only%20details")

    assert response.status_code == 200
    assert db.session.get(BookTitle, title.id).view_count == 0


def test_book_card_uses_declarative_actions_under_strict_csp(csrf_client):
    title = BookTitle(title="CSP card")
    cabinet = Cabinet(name="CSP display", type="display")
    admin = AdminUser(
        username="csp-admin",
        email="csp-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all([title, cabinet, admin])
    db.session.flush()
    db.session.add(Inventory(title_id=title.id, cabinet_id=cabinet.id, in_stock=True))
    db.session.commit()
    with csrf_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = admin.username
        sess["admin_id"] = admin.id
        sess["admin_role"] = admin.role

    response = csrf_client.get("/book_card/CSP%20card")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "onclick=" not in body
    assert 'data-ui-action="open-book-modal"' in body
    assert 'data-ui-action="open-cabinet-modal"' in body


def test_schema_migration_adds_legacy_book_title_columns(client):
    db.session.execute(text("DROP TABLE book_title"))
    db.session.execute(
        text(
            "CREATE TABLE book_title ("
            "id INTEGER PRIMARY KEY, "
            "title VARCHAR(255) UNIQUE NOT NULL, "
            "author VARCHAR(255)"
            ")"
        )
    )
    db.session.commit()

    app_module.ensure_title_cover_column()
    app_module.ensure_title_topics_column()

    columns = {column["name"] for column in db.inspect(db.engine).get_columns("book_title")}
    assert {"cover_link", "topics"}.issubset(columns)


def test_quantity_column_migration_preserves_every_inventory_row(client):
    title = BookTitle(title="Legacy inventory title")
    cabinet = Cabinet(name="Legacy inventory cabinet", type="display")
    db.session.add_all([title, cabinet])
    db.session.commit()
    db.session.execute(text("DROP TABLE inventory"))
    db.session.execute(
        text(
            "CREATE TABLE inventory ("
            "id INTEGER PRIMARY KEY, "
            "title_id INTEGER NOT NULL, "
            "cabinet_id INTEGER NOT NULL, "
            "qty_on_hand INTEGER NOT NULL, "
            "qty_reserved INTEGER NOT NULL, "
            "in_stock BOOLEAN NOT NULL, "
            "status TEXT NOT NULL, "
            "deleted_at DATETIME, "
            "created_at DATETIME, "
            "updated_at DATETIME"
            ")"
        )
    )
    db.session.execute(
        text(
            "INSERT INTO inventory "
            "(id, title_id, cabinet_id, qty_on_hand, qty_reserved, in_stock, status, deleted_at, created_at, updated_at) "
            "VALUES (1, :title_id, :cabinet_id, 3, 1, 0, 'archived', '2026-01-01', '2025-01-01', '2025-01-02')"
        ),
        {"title_id": title.id, "cabinet_id": cabinet.id},
    )
    db.session.commit()

    app_module.drop_quantity_columns_from_sqlite()
    db.session.expire_all()

    restored = db.session.get(Inventory, 1)
    assert Inventory.query.count() == 1
    assert restored.in_stock is False
    assert restored.status == "archived"
    assert restored.deleted_at is not None


def test_quantity_column_migration_rolls_back_on_duplicate_inventory(client):
    title = BookTitle(title="Duplicate legacy inventory")
    cabinet = Cabinet(name="Duplicate legacy cabinet", type="display")
    db.session.add_all([title, cabinet])
    db.session.commit()
    db.session.execute(text("DROP TABLE inventory"))
    db.session.execute(
        text(
            "CREATE TABLE inventory ("
            "id INTEGER PRIMARY KEY, "
            "title_id INTEGER NOT NULL, "
            "cabinet_id INTEGER NOT NULL, "
            "qty_on_hand INTEGER NOT NULL, "
            "qty_reserved INTEGER NOT NULL, "
            "in_stock BOOLEAN NOT NULL, "
            "status TEXT NOT NULL, "
            "deleted_at DATETIME, "
            "created_at DATETIME, "
            "updated_at DATETIME"
            ")"
        )
    )
    db.session.execute(
        text(
            "INSERT INTO inventory "
            "(id, title_id, cabinet_id, qty_on_hand, qty_reserved, in_stock, status) "
            "VALUES "
            "(1, :title_id, :cabinet_id, 1, 0, 1, 'active'), "
            "(2, :title_id, :cabinet_id, 1, 0, 1, 'active')"
        ),
        {"title_id": title.id, "cabinet_id": cabinet.id},
    )
    db.session.commit()

    with pytest.raises(IntegrityError):
        app_module.drop_quantity_columns_from_sqlite()

    columns = {column["name"] for column in db.inspect(db.engine).get_columns("inventory")}
    assert {"qty_on_hand", "qty_reserved"}.issubset(columns)
    assert db.session.execute(text("SELECT COUNT(*) FROM inventory")).scalar_one() == 2


def test_admin_add_book_preview_requires_auth(csrf_client):
    response = csrf_client.post(
        "/admin/add_book_preview",
        data={"title": "未授權測試"},
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 401
    assert response.get_json()["success"] is False


def _create_invite(code, expires_at):
    invite = AdminInvite(
        code=app_module.invite_reference(),
        code_hash=app_module.hash_invite_code(code),
        code_lookup=app_module.invite_code_lookup(code),
        role="admin",
        expires_at=expires_at,
    )
    db.session.add(invite)
    db.session.commit()
    return invite


def _registration_payload(code, **overrides):
    payload = {
        "username": "new-admin",
        "email": "new-admin@example.com",
        "password": "secure-password",
        "confirm_password": "secure-password",
        "security_code": code,
        "csrf_token": "test-csrf",
    }
    payload.update(overrides)
    return payload


def test_registration_rejects_expired_invites(csrf_client):
    invite = _create_invite("expired-invite", datetime.utcnow() - timedelta(minutes=1))

    response = csrf_client.post("/register", data=_registration_payload("expired-invite"))
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "註冊資訊無法驗證" in body
    assert db.session.get(AdminInvite, invite.id).used_at is None
    assert AdminUser.query.filter_by(username="new-admin").first() is None


def test_registration_failures_do_not_disclose_existing_account_details(csrf_client):
    db.session.add(
        AdminUser(
            username="existing-admin",
            email="existing@example.com",
            password_hash=generate_password_hash("pass"),
            role="admin",
        )
    )
    db.session.commit()
    _create_invite("valid-invite", datetime.utcnow() + timedelta(hours=1))

    response = csrf_client.post(
        "/register",
        data=_registration_payload(
            "valid-invite",
            username="existing-admin",
            email="different@example.com",
        ),
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "註冊資訊無法驗證" in body
    assert "此帳號已存在" not in body
    assert "此 Email 已存在" not in body


def test_registration_redeems_a_valid_invite_only_once(csrf_client):
    invite = _create_invite("redeem-once-invite", datetime.utcnow() + timedelta(hours=1))

    first_response = csrf_client.post(
        "/register",
        data=_registration_payload("redeem-once-invite"),
    )
    db.session.expire_all()
    redeemed = db.session.get(AdminInvite, invite.id)
    with csrf_client.session_transaction() as sess:
        sess.clear()
        sess["csrf_token"] = "test-csrf"
    second_response = csrf_client.post(
        "/register",
        data=_registration_payload(
            "redeem-once-invite",
            username="second-admin",
            email="second-admin@example.com",
        ),
    )

    assert first_response.status_code == 302
    assert AdminUser.query.filter_by(username="new-admin").one().role == "admin"
    assert redeemed.used_at is not None
    assert second_response.status_code == 200
    assert "註冊資訊無法驗證" in second_response.get_data(as_text=True)
    assert AdminUser.query.filter_by(username="second-admin").first() is None


def test_invite_claim_is_single_use(csrf_client):
    invite = _create_invite("single-use-invite", datetime.utcnow() + timedelta(hours=1))

    assert app_module.claim_invite(invite.id) is True
    db.session.commit()
    assert app_module.claim_invite(invite.id) is False
    db.session.rollback()


def test_security_migration_expires_legacy_invites(client):
    db.session.execute(text("DROP TABLE admin_invite"))
    db.session.execute(
        text(
            "CREATE TABLE admin_invite ("
            "id INTEGER PRIMARY KEY, "
            "code VARCHAR(32) UNIQUE NOT NULL, "
            "code_hash VARCHAR(255), "
            "code_lookup VARCHAR(64), "
            "memo VARCHAR(255), "
            "role VARCHAR(32), "
            "created_at DATETIME, "
            "used_at DATETIME"
            ")"
        )
    )
    db.session.execute(
        text(
            "INSERT INTO admin_invite "
            "(id, code, code_hash, code_lookup, role, created_at) "
            "VALUES (1, 'legacy-reference', 'legacy-hash', 'legacy-lookup', 'admin', :created_at)"
        ),
        {"created_at": datetime.utcnow()},
    )
    db.session.commit()

    app_module.apply_security_remediation_migration()

    columns = {column["name"] for column in db.inspect(db.engine).get_columns("admin_invite")}
    expires_at = db.session.execute(
        text("SELECT expires_at FROM admin_invite WHERE id = 1")
    ).scalar_one()
    assert "expires_at" in columns
    assert expires_at is not None


def test_issue_report_persists_separately_from_audit_log(csrf_client):
    response = csrf_client.post(
        "/api/report_issue",
        json={"name": "tester", "type": "bug", "description": "button failed"},
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    report = IssueReport.query.one()
    assert report.category == "bug"
    assert report.description == "button failed"
    assert AuditLog.query.filter_by(action="issue_report").count() == 0


def test_issue_report_rejects_non_json_and_unexpected_fields(csrf_client):
    non_json_response = csrf_client.post(
        "/api/report_issue",
        data="not json",
        content_type="text/plain",
        headers={"X-CSRF-Token": "test-csrf"},
    )
    extra_field_response = csrf_client.post(
        "/api/report_issue",
        json={
            "name": "tester",
            "type": "bug",
            "description": "button failed",
            "unexpected": "value",
        },
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert non_json_response.status_code == 415
    assert extra_field_response.status_code == 400
    assert IssueReport.query.count() == 0


def test_issue_report_rejects_control_characters(csrf_client):
    response = csrf_client.post(
        "/api/report_issue",
        json={"name": "tester", "type": "bug", "description": "button\u0000failed"},
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 400
    assert IssueReport.query.count() == 0


def test_issue_report_honeypot_and_deduplication(csrf_client):
    payload = {"name": "tester", "type": "bug", "description": "button failed"}
    honeypot_response = csrf_client.post(
        "/api/report_issue",
        json={**payload, "website": "https://spam.invalid"},
        headers={"X-CSRF-Token": "test-csrf"},
    )
    first_response = csrf_client.post(
        "/api/report_issue",
        json=payload,
        headers={"X-CSRF-Token": "test-csrf"},
    )
    repeated_response = csrf_client.post(
        "/api/report_issue",
        json=payload,
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert honeypot_response.status_code == 200
    assert first_response.status_code == 200
    assert repeated_response.status_code == 200
    assert IssueReport.query.count() == 1


def test_advance_admin_can_review_escaped_issue_reports(csrf_client):
    user = AdminUser(
        username="issue-reviewer",
        email="issue-reviewer@example.com",
        password_hash=generate_password_hash("pass"),
        role="advance-admin",
    )
    db.session.add_all(
        [
            user,
            IssueReport(
                reporter_name="reporter",
                category="bug",
                description='<img src=x onerror="alert(1)">',
                source_path="/",
                fingerprint="a" * 64,
            ),
        ]
    )
    db.session.commit()
    with csrf_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = user.username
        sess["admin_id"] = user.id
        sess["admin_role"] = user.role

    response = csrf_client.get("/admin/system")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "公開問題回報" in body
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in body


def test_standard_admin_cannot_view_issue_reports(csrf_client):
    user = AdminUser(
        username="standard-admin",
        email="standard-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all(
        [
            user,
            IssueReport(
                reporter_name="reporter",
                category="bug",
                description="private public-report content",
                source_path="/",
                fingerprint="b" * 64,
            ),
        ]
    )
    db.session.commit()
    with csrf_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = user.username
        sess["admin_id"] = user.id
        sess["admin_role"] = user.role

    response = csrf_client.get("/admin/system")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "公開問題回報" not in body
    assert "private public-report content" not in body


def test_login_clears_existing_session_and_rotates_csrf(client):
    user = AdminUser(
        username="admin",
        email="admin@example.com",
        password_hash=generate_password_hash("correct-pass"),
        role="admin",
    )
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "old-csrf"
        sess["pre_login_marker"] = "must-be-cleared"

    response = client.post(
        "/login",
        data={"username": "admin", "password": "correct-pass"},
        headers={"X-CSRF-Token": "old-csrf"},
    )

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["is_admin"] is True
        assert sess["admin_user"] == "admin"
        assert "pre_login_marker" not in sess
        assert sess["csrf_token"] != "old-csrf"


def test_cover_urls_are_allowlisted():
    assert is_allowed_cover_url("https://imgs.cwgv.com.tw/book/cover.jpg")
    assert is_allowed_cover_url("https://bookzone.cwgv.com.tw/assets/cover.jpg")
    assert is_allowed_cover_url("https://static.cwgv.com.tw/assets/cover.jpg")
    assert normalize_cover_url("http://imgs.cwgv.com.tw/book/cover.jpg") == "https://imgs.cwgv.com.tw/book/cover.jpg"
    assert normalize_cover_url("//imgs.cwgv.com.tw/book/cover.jpg") == "https://imgs.cwgv.com.tw/book/cover.jpg"
    assert normalize_cover_url("https://evil.example/pixel.png") == ""
    assert normalize_cover_url("//evil.example/pixel.png") == ""
    assert not is_allowed_cover_url("javascript:alert(1)")

    title = BookTitle(title="Unsafe Cover", cover_link="https://evil.example/pixel.png")
    assert cover_url_for_title(title) == COVER_PLACEHOLDER_URL


def test_external_links_use_noopener_and_noreferrer():
    template = Path("templates/base.html").read_text(encoding="utf-8")

    assert 'target="_blank" rel="noopener noreferrer"' in template


def test_frontend_sources_do_not_use_html_rendering_sinks():
    forbidden_sinks = ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write")
    sources = [*Path("static/js").rglob("*.js"), *Path("templates").rglob("*.html")]

    for source in sources:
        content = source.read_text(encoding="utf-8")
        for sink in forbidden_sinks:
            assert sink not in content, f"{source} contains forbidden DOM sink {sink}"


def test_service_worker_does_not_cache_cover_critical_assets():
    worker = Path("static/sw.js").read_text(encoding="utf-8")

    assert "url.origin !== self.location.origin" in worker
    assert "startsWith('/static/css/')" in worker
    assert "startsWith('/static/js/')" in worker
    assert "cache: 'no-store'" in worker
    assert "'/static/css/main.css'" not in worker
    assert "'/static/js/base.js'" not in worker


def test_upload_limit_returns_controlled_413(csrf_client):
    csrf_client.application.config["MAX_CONTENT_LENGTH"] = 32

    response = csrf_client.post(
        "/api/report_issue",
        json={"name": "tester", "type": "bug", "description": "x" * 200},
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 413
    assert response.get_json()["message"] == "上傳檔案過大，請縮小後再試。"


def test_advance_admin_can_access_audit_export(csrf_client):
    user = AdminUser(
        username="root",
        email="root@example.com",
        password_hash=generate_password_hash("pass"),
        role="advance-admin",
    )
    db.session.add(user)
    db.session.commit()
    with csrf_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = user.username
        sess["admin_id"] = user.id
        sess["admin_role"] = user.role

    response = csrf_client.get("/admin/audit/export")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"


def test_deleted_admin_session_is_invalidated(csrf_client):
    user = AdminUser(
        username="stale",
        email="stale@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add(user)
    db.session.commit()
    user_id = user.id
    with csrf_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = user.username
        sess["admin_id"] = user_id
        sess["admin_role"] = user.role

    db.session.delete(user)
    db.session.commit()

    response = csrf_client.get("/admin")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    with csrf_client.session_transaction() as sess:
        assert "is_admin" not in sess


def test_csv_cells_are_spreadsheet_safe():
    assert csv_safe_cell("=HYPERLINK(\"https://evil.example\")").startswith("\t=")
    assert csv_safe_cell("  +SUM(1,1)").startswith("\t  +")
    assert csv_safe_cell("\n=SUM(1,1)") == "\t =SUM(1,1)"
    assert csv_safe_cell("normal title") == "normal title"


def test_audit_export_sanitizes_formula_cells(csrf_client):
    user = AdminUser(
        username="root",
        email="root-csv@example.com",
        password_hash=generate_password_hash("pass"),
        role="advance-admin",
    )
    db.session.add(user)
    db.session.add(
        AuditLog(
            actor="=cmd",
            action="+open",
            target="@target",
            details="-formula",
        )
    )
    db.session.commit()
    with csrf_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = user.username
        sess["admin_id"] = user.id
        sess["admin_role"] = user.role

    response = csrf_client.get("/admin/audit/export")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert rows[1][1].startswith("\t=")
    assert rows[1][2].startswith("\t+")
    assert rows[1][3].startswith("\t@")
    assert rows[1][4].startswith("\t-")


def test_pg_dump_env_does_not_require_url_argument():
    env = pg_env_from_database_url("postgresql://user:secret@example.com:5432/dbname?sslmode=require")

    assert env["PGHOST"] == "example.com"
    assert env["PGPORT"] == "5432"
    assert env["PGUSER"] == "user"
    assert env["PGPASSWORD"] == "secret"
    assert env["PGDATABASE"] == "dbname"
    assert env["PGSSLMODE"] == "require"


def test_runtime_requirements_exclude_psycopg2_binary():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()

    assert "psycopg2-binary" not in requirements
    assert "psycopg2==2.9.9" in requirements


def test_hosted_production_does_not_auto_seed_default_admin(monkeypatch, client):
    monkeypatch.setattr(app_module, "STRICT_HOSTED_PRODUCTION", True)
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD_RAW", None)
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD_HASH", generate_password_hash("seed-pass"))
    monkeypatch.delenv("EXIS_ENABLE_ADMIN_BOOTSTRAP", raising=False)

    app_module.ensure_default_admin()

    assert AdminUser.query.count() == 0


def test_hosted_production_does_not_auto_promote_advance_admin(monkeypatch, client):
    user = AdminUser(
        username="manager",
        email="manager@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add(user)
    db.session.commit()
    monkeypatch.setattr(app_module, "STRICT_HOSTED_PRODUCTION", True)
    monkeypatch.delenv("EXIS_ENABLE_ADMIN_BOOTSTRAP", raising=False)

    app_module.ensure_advance_admin_exists()

    assert AdminUser.query.filter_by(username="manager").one().role == "admin"
