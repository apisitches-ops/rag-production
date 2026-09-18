from fastapi.testclient import TestClient

from app.ingest import ingest_document
from app.main import app
from conftest import make_pdf, node_ids_for

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


def test_post_query_filters_by_acting_role(tmp_path):
    restricted_pdf = tmp_path / "restricted.pdf"
    make_pdf(str(restricted_pdf), "The confidential internal budget code is ZX-9942.")

    restricted_id = ingest_document(
        str(restricted_pdf), document_name="restricted_doc", acl_group="finance"
    )

    restricted_node_ids = node_ids_for(restricted_id)

    client = TestClient(app)
    query = "What is the confidential internal budget code?"

    no_role = client.post("/query", json={"query": query})
    assert not set(no_role.json()["citations"]) & restricted_node_ids

    right_role = client.post("/query", json={"query": query, "acting_role": "finance"})
    assert set(right_role.json()["citations"]) & restricted_node_ids
