import pytest

from app import app, db
from database.models import Cabinet, BookTitle, Inventory


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


def test_move_book_from_reserve_to_display(client):
    reserve = Cabinet(name="Reserve", type="reserve")
    display = Cabinet(name="Display", type="display")
    title = BookTitle(title="測試書籍", author="測試作者")
    db.session.add_all([reserve, display, title])
    db.session.flush()

    book = Inventory(title_id=title.id, cabinet_id=reserve.id, in_stock=True, status="active")
    db.session.add(book)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["csrf_token"] = "testtoken"

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
