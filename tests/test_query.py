from app.ingest import ingest_document
from app.query import answer_query

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
