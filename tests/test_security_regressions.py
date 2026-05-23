import json

from database.models import AdminUser, AuditLog, db
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
