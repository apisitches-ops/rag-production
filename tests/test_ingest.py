import psycopg

from app.db import DATABASE_URL
from app.ingest import _extract_csv_text, ingest_document

FIXTURE_PDF = "tests/fixtures/sample.pdf"
FIXTURE_CSV = "tests/fixtures/sample.csv"
FIXTURE_RAGGED_CSV = "tests/fixtures/ragged.csv"


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


def test_ingest_document_csv_stores_parent_and_child_nodes():
    document_id = ingest_document(FIXTURE_CSV)

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT parent_id, embedding IS NOT NULL FROM nodes WHERE document_id = %s",
            (document_id,),
        ).fetchall()

    child_count = sum(1 for _, embedded in rows if embedded)
    assert child_count > 1, "expected the fixture CSV to chunk into multiple embedded child Nodes"
    assert any(parent_id is None for parent_id, _ in rows), "expected at least one Node with no parent"


def test_extract_csv_text_skips_missing_and_extra_fields_instead_of_stringifying_none():
    text = _extract_csv_text(FIXTURE_RAGGED_CSV)

    assert "None" not in text
    assert "extra" not in text and "fields" not in text
    assert "Bangkok" in text


def test_ingest_document_acl_group_defaults_to_none():
    document_id = ingest_document(FIXTURE_PDF)

    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            "SELECT acl_group FROM documents WHERE id = %s", (document_id,)
        ).fetchone()

    assert row == (None,)


def test_ingest_document_stores_acl_group():
    document_id = ingest_document(FIXTURE_PDF, acl_group="finance")

    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            "SELECT acl_group FROM documents WHERE id = %s", (document_id,)
        ).fetchone()

    assert row == ("finance",)
