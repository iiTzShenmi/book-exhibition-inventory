"""Regression coverage for the Goal 3 public and administrator UX work."""

from pathlib import Path

from app import db
from database.models import BookTitle, Cabinet, Inventory


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_homepage_uses_search_first_workspace_and_no_map_placeholder(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-search-workspace' in page
    assert 'id="search-status"' in page
    assert 'data-location-picker' in page
    assert "floor_plan_placeholder.png" not in page
    assert 'id="venue-map-modal"' not in page
    assert 'id="announcement-overlay"' not in page


def test_search_results_expose_result_summary_and_active_filter(client):
    cabinet = Cabinet(name="UX Test Shelf", type="書櫃")
    title = BookTitle(title="UX Regression Book", author="EXIS")
    db.session.add_all([cabinet, title])
    db.session.flush()
    db.session.add(Inventory(title_id=title.id, cabinet_id=cabinet.id, in_stock=True))
    db.session.commit()

    response = client.get("/search?q=UX%20Regression&cabinet=UX%20Test%20Shelf")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="result-summary"' in page
    assert 'class="active-filters"' in page
    assert 'data-clear-all-filters' in page
    assert "1 筆結果" in page
    assert "UX Test Shelf" in page


def test_public_form_markup_has_bounded_input_and_live_feedback():
    template = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert 'maxlength="80"' in template
    assert 'maxlength="1200"' in template
    assert 'data-issue-counter' in template
    assert 'aria-live="polite"' in template


def test_auth_templates_clarify_invitation_flow_without_changing_security_contract():
    login_template = (PROJECT_ROOT / "templates" / "login.html").read_text(encoding="utf-8")
    register_template = (PROJECT_ROOT / "templates" / "register.html").read_text(
        encoding="utf-8"
    )

    assert 'autocomplete="username"' in login_template
    assert 'autocomplete="current-password"' in login_template
    assert "收到邀請安全碼" in register_template
    assert 'autocomplete="one-time-code"' in register_template
    assert 'minlength="6"' in register_template


def test_admin_modals_show_context_and_accessible_status_regions():
    template = (PROJECT_ROOT / "templates" / "admin_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'id="move-book-source"' in template
    assert 'id="move-book-status"' in template
    assert 'aria-live="polite"' in template
    script = (PROJECT_ROOT / "static" / "js" / "admin_dashboard.js").read_text(
        encoding="utf-8"
    )
    assert "封存紀錄" in script


def test_admin_dashboard_has_empty_search_and_inline_form_status_regions():
    template = (PROJECT_ROOT / "templates" / "admin_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'data-admin-search-empty' in template
    assert 'id="add-book-status"' in template
    assert 'id="cabinet-form-status"' in template


def test_scanner_focus_does_not_interrupt_active_users():
    script = (PROJECT_ROOT / "static" / "js" / "base.js").read_text(encoding="utf-8")

    assert "setInterval(() => targetInput.focus()" not in script
