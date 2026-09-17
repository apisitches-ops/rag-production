import psycopg

from app.db import DATABASE_URL
from app.ingest import ingest_document

FIXTURE_PDF = "tests/fixtures/sample.pdf"


def test_ingest_document_stores_parent_and_child_nodes():
    document_id = ingest_document(FIXTURE_PDF)

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT parent_id, embedding IS NOT NULL FROM nodes WHERE document_id = %s",
            (document_id,),
        ).fetchall()

    child_count = sum(1 for _, embedded in rows if embedded)
    assert child_count > 1, "expected the fixture PDF to chunk into multiple embedded child Nodes"
    assert any(parent_id is None for parent_id, _ in rows), "expected at least one Node with no parent"
