from app.ingest import ingest_document
from app.query import _looks_truncated, _merge_with_overlap, answer_query
from conftest import make_pdf, node_ids_for

FIXTURE_PDF = "tests/fixtures/sample.pdf"


def test_looks_truncated_flags_a_bare_trailing_decimal_point():
    # The confirmed real pattern (#31): a Node ending mid-number with a
    # decimal point and no digit after it.
    assert _looks_truncated("The refining margin averaged 6.") is True
    assert _looks_truncated("ที่เฉลี่ยที่ระดับ 6.") is True


def test_looks_truncated_does_not_flag_complete_sentences():
    assert _looks_truncated("The refining margin averaged 6.8 dollars.") is False
    assert _looks_truncated("Revenue reached 42%.") is False
    assert _looks_truncated("The company grew significantly.") is False


def test_merge_with_overlap_drops_the_duplicated_prefix():
    # chunk_overlap re-includes a trailing run of `content` at the start of
    # `next_content` (#31 follow-up, code review on #38) — a bare append
    # would duplicate "sat at 6." here.
    content = "The margin sat at 6."
    next_content = "sat at 6.8 dollars per barrel."

    assert _merge_with_overlap(content, next_content) == "The margin sat at 6.8 dollars per barrel."


def test_merge_with_overlap_appends_bare_when_there_is_no_overlap():
    assert _merge_with_overlap("The margin sat at 6.", "8 dollars.") == "The margin sat at 6.8 dollars."


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


def test_answer_query_stitches_in_the_real_next_node_for_a_truncated_fact():
    # A real excerpt from the PTT One Report 2024 Document (#31): under
    # chunk_overlap=30, a Node still ends "...เฉลี่ยที่ระดับ 6." with the
    # true continuation ("8 เหรียญสหรัฐต่อบาร์เรล") landing in its actual
    # next-sibling Node. Without stitching, the answer stops mid-number
    # instead of completing it — verified separately against this exact
    # fixture before this fix was implemented.
    ingest_document("tests/fixtures/truncated_fact.csv")

    result = answer_query(
        "ค่าการกลั่นของโรงกลั่นประเภท Cracking ที่สิงคโปร์ปี 2567 เฉลี่ยเท่าไหร่ เทียบกับปี 2566"
    )

    assert "6.8" in result["answer"], (
        "expected the real next-sibling Node's continuation to be stitched in, "
        "completing the truncated fact with its correct value"
    )


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
