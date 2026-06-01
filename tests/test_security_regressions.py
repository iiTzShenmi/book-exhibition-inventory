import json
import csv
import io
from pathlib import Path

import app as app_module
from app import (
    COVER_PLACEHOLDER_URL,
    cover_url_for_title,
    csv_safe_cell,
    is_allowed_cover_url,
    normalize_cover_url,
    pg_env_from_database_url,
)
from database.models import AdminUser, AuditLog, BookTitle, db
from werkzeug.security import generate_password_hash


def test_security_headers_are_present(client):
    response = client.get("/")

    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src-attr 'none'" in csp
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_login_requires_csrf(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong"})

    assert response.status_code == 400


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


def test_admin_add_book_preview_requires_auth(csrf_client):
    response = csrf_client.post(
        "/admin/add_book_preview",
        data={"title": "未授權測試"},
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 401
    assert response.get_json()["success"] is False


def test_issue_report_persists_with_csrf(csrf_client):
    response = csrf_client.post(
        "/api/report_issue",
        json={"name": "tester", "type": "bug", "description": "button failed"},
        headers={"X-CSRF-Token": "test-csrf"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    log = AuditLog.query.filter_by(action="issue_report").one()
    details = json.loads(log.details)
    assert details["type"] == "bug"
    assert details["description"] == "button failed"


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
