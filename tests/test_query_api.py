from fastapi.testclient import TestClient

from app.ingest import ingest_document
from app.main import app

FIXTURE_PDF = "tests/fixtures/sample.pdf"


def test_post_query_returns_grounded_answer():
    ingest_document(FIXTURE_PDF)
    client = TestClient(app)

    response = client.post("/query", json={"query": "What year does PTT aim to achieve Net Zero emissions?"})

    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is False
    assert "2050" in body["answer"]
    assert len(body["citations"]) > 0
