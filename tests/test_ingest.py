import csv

import psycopg

from app.db import DATABASE_URL
from app.ingest import _extract_csv_text, ingest_document

FIXTURE_PDF = "tests/fixtures/sample.pdf"
FIXTURE_CSV = "tests/fixtures/sample.csv"
FIXTURE_RAGGED_CSV = "tests/fixtures/ragged.csv"
FIXTURE_SINGLE_COLUMN_CSV = "tests/fixtures/single_column.csv"


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


def test_extract_csv_text_skips_missing_extra_and_blank_fields():
    text = _extract_csv_text(FIXTURE_RAGGED_CSV)

    assert "None" not in text
    assert "extra" not in text and "fields" not in text
    assert "id: 4" in text.splitlines(), "blank question/answer fields should be dropped, not stringified empty"
    assert "Bangkok" in text


def test_extract_csv_text_does_not_prefix_a_single_column_csv_with_its_header():
    text = _extract_csv_text(FIXTURE_SINGLE_COLUMN_CSV)

    assert text == "PTT committed to achieving Net Zero greenhouse gas emissions by the year 2050."


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


def test_ingest_document_stores_next_id_for_sibling_nodes():
    document_id = ingest_document(FIXTURE_PDF)

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT id, next_id FROM nodes WHERE document_id = %s", (document_id,)
        ).fetchall()

    ids = {row_id for row_id, _ in rows}
    next_ids = {next_id for _, next_id in rows if next_id is not None}
    assert next_ids, "expected at least one Node to have a next_id"
    assert next_ids <= ids, "every next_id should point at another Node from the same Document"


def test_ingest_document_node_overlap_keeps_a_boundary_straddling_fact_intact(tmp_path):
    # This exact filler length was found empirically to land a Node boundary
    # right between "4." and "9 dollars" at chunk_overlap=20 (the library's
    # implicit default) — reproducing the real bug (#31): a numeric fact
    # split across Nodes, with no Node containing it whole.
    fact_sentence = "The refining margin in 2024 averaged 4.\n9 dollars per barrel."
    filler = (
        "The quarterly report discusses many unrelated topics such as logistics, "
        "supply chain management, and workforce planning across several business units. "
        * 100
    )
    text = filler[:2990] + fact_sentence

    csv_path = tmp_path / "boundary_fact.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["content"])
        writer.writerow([text])

    document_id = ingest_document(str(csv_path))

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT content FROM nodes WHERE document_id = %s AND embedding IS NOT NULL",
            (document_id,),
        ).fetchall()

    assert any(
        "averaged 4." in content
        and "9 dollars per barrel" in content
        and content.index("averaged 4.") < content.index("9 dollars per barrel")
        for (content,) in rows
    ), "expected the fact to appear intact within at least one Node despite the boundary that used to split it"
