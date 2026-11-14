import pytest
from fastapi.testclient import TestClient

ADMIN = ("admin", "test-admin-pw")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import config, sandbox
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setattr(config, "ANON_HMAC_KEY", "test-key")
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "MIN_SECONDS_BETWEEN_POSTS", 0)
    sandbox._hist.clear()
    from app.main import app
    # https base URL so the Secure cookie is sent back by the test client
    with TestClient(app, base_url="https://testserver") as c:
        yield c
