import os

import pytest

os.environ.setdefault("APP_ENV", "testing")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("EXIS_SKIP_STARTUP_INIT", "1")
os.environ.setdefault("EXIS_REQUEST_SCHEMA_CHECK", "0")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

from app import app as flask_app, db  # noqa: E402


@pytest.fixture
def client():
    flask_app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    ctx = flask_app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    with flask_app.test_client() as test_client:
        yield test_client
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture
def csrf_client(client):
    with client.session_transaction() as sess:
        sess["csrf_token"] = "test-csrf"
    return client
