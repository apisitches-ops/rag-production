from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_health_returns_ok_when_postgres_is_reachable():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_error_when_postgres_is_unreachable(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://rag:rag@localhost:1/rag")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error"}
