from datetime import datetime

import pytest

from app import app, db
from database.models import AdminUser, Cabinet, BookTitle, Inventory
from werkzeug.security import generate_password_hash


@pytest.fixture
def client():
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        WTF_CSRF_ENABLED=False,
    )
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    yield app.test_client()
    db.session.remove()
    db.drop_all()
    ctx.pop()


def authenticate(client, admin):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_user"] = admin.username
        sess["admin_id"] = admin.id
        sess["admin_role"] = admin.role
        sess["csrf_token"] = "testtoken"


def test_move_book_from_reserve_to_display(client):
    reserve = Cabinet(name="Reserve", type="reserve")
    display = Cabinet(name="Display", type="display")
    title = BookTitle(title="測試書籍", author="測試作者")
    admin = AdminUser(
        username="move-admin",
        email="move-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all([reserve, display, title, admin])
    db.session.flush()

    book = Inventory(title_id=title.id, cabinet_id=reserve.id, in_stock=True, status="active")
    db.session.add(book)
    db.session.commit()

    authenticate(client, admin)

    resp = client.patch(
        f"/cabinets/{reserve.id}/books/{book.id}/move",
        json={"target_cabinet_id": display.id},
        headers={"X-CSRF-Token": "testtoken"},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["target_cabinet_id"] == display.id

    active_inventory = Inventory.query.filter_by(title_id=title.id, status="active").all()
    assert len(active_inventory) == 1
    assert active_inventory[0].cabinet_id == display.id
    assert (
        Inventory.query.filter_by(cabinet_id=reserve.id, status="active").first()
        is None
    )


def test_replenish_rejects_a_display_cabinet_as_source(client):
    source = Cabinet(name="Not reserve", type="display")
    target = Cabinet(name="Display target", type="display")
    title = BookTitle(title="Replenish title")
    admin = AdminUser(
        username="replenish-admin",
        email="replenish-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all([source, target, title, admin])
    db.session.flush()
    source_book = Inventory(title_id=title.id, cabinet_id=source.id, in_stock=True)
    db.session.add(source_book)
    db.session.commit()
    authenticate(client, admin)

    response = client.post(
        f"/replenish/{title.title}",
        json={
            "display_cabinet_id": target.id,
            "reserve_cabinet_id": source.id,
            "reserve_book_id": source_book.id,
        },
        headers={"X-CSRF-Token": "testtoken"},
    )

    assert response.status_code == 400
    assert "備書櫃" in response.get_json()["message"]
    assert db.session.get(Inventory, source_book.id).cabinet_id == source.id


def test_replenish_rejects_a_title_mismatch(client):
    reserve = Cabinet(name="Reserve source", type="reserve")
    target = Cabinet(name="Display target", type="display")
    title = BookTitle(title="Expected title")
    admin = AdminUser(
        username="title-check-admin",
        email="title-check-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all([reserve, target, title, admin])
    db.session.flush()
    reserve_book = Inventory(title_id=title.id, cabinet_id=reserve.id, in_stock=True)
    db.session.add(reserve_book)
    db.session.commit()
    authenticate(client, admin)

    response = client.post(
        "/replenish/Unexpected%20title",
        json={
            "display_cabinet_id": target.id,
            "reserve_cabinet_id": reserve.id,
            "reserve_book_id": reserve_book.id,
        },
        headers={"X-CSRF-Token": "testtoken"},
    )

    assert response.status_code == 400
    assert "書名" in response.get_json()["message"]
    assert db.session.get(Inventory, reserve_book.id).cabinet_id == reserve.id


def test_delete_cabinet_rejects_retained_archived_inventory(client):
    cabinet = Cabinet(name="Archived history", type="display")
    title = BookTitle(title="Archived book")
    admin = AdminUser(
        username="cabinet-delete-admin",
        email="cabinet-delete-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all([cabinet, title, admin])
    db.session.flush()
    db.session.add(
        Inventory(
            title_id=title.id,
            cabinet_id=cabinet.id,
            status="archived",
            in_stock=False,
        )
    )
    db.session.commit()
    authenticate(client, admin)

    response = client.delete(
        f"/cabinets/{cabinet.id}",
        headers={"X-CSRF-Token": "testtoken"},
    )

    assert response.status_code == 400
    assert Cabinet.query.get(cabinet.id) is not None


def test_legacy_quantity_endpoints_reject_unsupported_changes(client):
    cabinet = Cabinet(name="Quantity cabinet", type="display")
    title = BookTitle(title="Quantity title")
    admin = AdminUser(
        username="quantity-admin",
        email="quantity-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all([cabinet, title, admin])
    db.session.flush()
    book = Inventory(title_id=title.id, cabinet_id=cabinet.id, in_stock=True)
    db.session.add(book)
    db.session.commit()
    authenticate(client, admin)

    adjust_response = client.patch(
        f"/cabinets/{cabinet.id}/books/{book.id}/adjust",
        json={"delta": 1},
        headers={"X-CSRF-Token": "testtoken"},
    )
    add_response = client.post(
        "/add_book",
        data={"title": "Another title", "cabinet_id": cabinet.id, "amount": "2"},
        headers={"X-CSRF-Token": "testtoken"},
    )

    assert adjust_response.status_code == 409
    assert add_response.status_code == 400
    assert db.session.get(Inventory, book.id).status == "active"
    assert BookTitle.query.filter_by(title="Another title").first() is None


def test_add_book_restores_archived_inventory_record(client):
    cabinet = Cabinet(name="Restore cabinet", type="display")
    title = BookTitle(title="Restore archived book")
    admin = AdminUser(
        username="restore-admin",
        email="restore-admin@example.com",
        password_hash=generate_password_hash("pass"),
        role="admin",
    )
    db.session.add_all([cabinet, title, admin])
    db.session.flush()
    archived = Inventory(
        title_id=title.id,
        cabinet_id=cabinet.id,
        status="archived",
        in_stock=False,
        deleted_at=datetime.utcnow(),
    )
    db.session.add(archived)
    db.session.commit()
    authenticate(client, admin)

    response = client.post(
        "/add_book",
        data={"title": title.title, "cabinet_id": cabinet.id, "amount": "1"},
        headers={"X-CSRF-Token": "testtoken"},
    )

    db.session.expire_all()
    restored = db.session.get(Inventory, archived.id)
    assert response.status_code == 200
    assert response.get_json()["created"] is False
    assert restored.status == "active"
    assert restored.in_stock is True
    assert restored.deleted_at is None
