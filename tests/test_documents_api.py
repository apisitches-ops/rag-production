import psycopg
from fastapi.testclient import TestClient

from app.db import DATABASE_URL
from app.main import app

FIXTURE_PDF = "tests/fixtures/sample.pdf"
FIXTURE_CSV = "tests/fixtures/sample.csv"


def test_post_documents_ingests_uploaded_pdf():
    client = TestClient(app)

    with open(FIXTURE_PDF, "rb") as f:
        response = client.post("/documents", files={"file": ("sample.pdf", f, "application/pdf")})

    assert response.status_code == 200
    document_id = response.json()["document_id"]

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT id FROM nodes WHERE document_id = %s", (document_id,)
        ).fetchall()
        path = conn.execute(
            "SELECT path FROM documents WHERE id = %s", (document_id,)
        ).fetchone()

    assert len(rows) > 0
    assert path == ("sample.pdf",)


def test_post_documents_stores_acl_group():
    client = TestClient(app)

    with open(FIXTURE_PDF, "rb") as f:
        response = client.post(
            "/documents",
            files={"file": ("sample.pdf", f, "application/pdf")},
            data={"acl_group": "finance"},
        )

    assert response.status_code == 200
    document_id = response.json()["document_id"]

    with psycopg.connect(DATABASE_URL) as conn:
        acl_group = conn.execute(
            "SELECT acl_group FROM documents WHERE id = %s", (document_id,)
        ).fetchone()

    assert acl_group == ("finance",)


def test_post_documents_normalizes_empty_acl_group_to_none():
    client = TestClient(app)

    with open(FIXTURE_PDF, "rb") as f:
        response = client.post(
            "/documents",
            files={"file": ("sample.pdf", f, "application/pdf")},
            data={"acl_group": ""},
        )

    assert response.status_code == 200
    document_id = response.json()["document_id"]

    with psycopg.connect(DATABASE_URL) as conn:
        acl_group = conn.execute(
            "SELECT acl_group FROM documents WHERE id = %s", (document_id,)
        ).fetchone()

    assert acl_group == (None,)


def test_post_documents_ingests_uploaded_csv():
    client = TestClient(app)

    with open(FIXTURE_CSV, "rb") as f:
        response = client.post("/documents", files={"file": ("sample.csv", f, "text/csv")})

    assert response.status_code == 200
    document_id = response.json()["document_id"]

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT id FROM nodes WHERE document_id = %s", (document_id,)
        ).fetchall()

    assert len(rows) > 0


def test_post_documents_rejects_unparseable_upload():
    client = TestClient(app)

    response = client.post(
        "/documents", files={"file": ("bad.pdf", b"not a real pdf", "application/pdf")}
    )

    assert response.status_code == 400
