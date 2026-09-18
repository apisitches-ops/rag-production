from app.ingest import ingest_document
from app.query import answer_query
from conftest import make_pdf, node_ids_for

FIXTURE_PDF = "tests/fixtures/sample.pdf"


def test_answer_query_returns_grounded_answer_with_citations():
    ingest_document(FIXTURE_PDF)

    result = answer_query("What year does PTT aim to achieve Net Zero emissions?")

    assert result["abstained"] is False
    assert "2050" in result["answer"]
    assert len(result["citations"]) > 0


def test_answer_query_abstains_when_context_is_insufficient():
    ingest_document(FIXTURE_PDF)

    result = answer_query("What is the favorite color of PTT's chief financial officer?")

    assert result["abstained"] is True


def test_answer_query_filters_by_acting_role(tmp_path):
    public_pdf = tmp_path / "public.pdf"
    make_pdf(str(public_pdf), "The company picnic is scheduled for June 5th every year.")
    restricted_pdf = tmp_path / "restricted.pdf"
    make_pdf(str(restricted_pdf), "The confidential internal budget code is ZX-9942.")

    ingest_document(str(public_pdf), document_name="public_doc")
    restricted_id = ingest_document(
        str(restricted_pdf), document_name="restricted_doc", acl_group="finance"
    )

    restricted_node_ids = node_ids_for(restricted_id)

    query = "What is the confidential internal budget code?"

    no_role = answer_query(query)
    assert not set(no_role["citations"]) & restricted_node_ids

    wrong_role = answer_query(query, acting_role="other")
    assert not set(wrong_role["citations"]) & restricted_node_ids

    right_role = answer_query(query, acting_role="finance")
    assert set(right_role["citations"]) & restricted_node_ids
    assert "ZX-9942" in right_role["answer"]
