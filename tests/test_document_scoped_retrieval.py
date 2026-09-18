import app.query as query_module
from app.ingest import ingest_document
from app.ollama import embed
from app.query import _select_documents
from conftest import make_pdf, node_ids_for


def test_select_documents_ranks_the_relevant_document_first(tmp_path):
    relevant_pdf = tmp_path / "relevant.pdf"
    make_pdf(str(relevant_pdf), "PTT committed to achieving Net Zero greenhouse gas emissions by the year 2050.")
    relevant_id = ingest_document(str(relevant_pdf))

    # Several distractor Documents so the real corpus is larger than
    # DOCUMENT_CANDIDATES (3) — exercises the real, unmocked ranking+narrowing
    # path with a corpus that doesn't fit entirely within the returned limit.
    distractor_texts = [
        "The company picnic is scheduled for June 5th every year.",
        "Quarterly maintenance for the east wing elevator is due next month.",
        "The office recycling program starts on the first Monday of each quarter.",
        "Parking permits must be renewed annually at the front desk.",
    ]
    for i, text in enumerate(distractor_texts):
        distractor_pdf = tmp_path / f"distractor_{i}.pdf"
        make_pdf(str(distractor_pdf), text)
        ingest_document(str(distractor_pdf))

    query = "What year does PTT aim to achieve Net Zero emissions?"
    query_embedding = embed([query])[0]

    result = _select_documents(query, query_embedding, acting_role=None, limit=3)

    assert len(result) == 3
    assert result[0] == relevant_id


def test_select_documents_gives_real_weight_to_the_bm25_signal(tmp_path):
    # Mirrors the chunk-level hybrid test (tests/test_hybrid_retrieval.py):
    # a rare literal token dense similarity alone won't rank highly, that
    # BM25 (exact term match) should. Several unrelated filler Documents are
    # included so the BM25/dense rank populations differ meaningfully — a
    # regression test for the fix where BM25 document-ranks were previously
    # drawn from the much larger node-level population instead of being
    # re-ranked at the Document level, diluting the lexical signal to near
    # nothing relative to dense.
    bm25_pdf = tmp_path / "bm25.pdf"
    make_pdf(str(bm25_pdf), "zzqorbnex zzqorbnex zzqorbnex flibbertigibbet wombatronic filler text.")
    bm25_id = ingest_document(str(bm25_pdf))

    filler_texts = [
        "The company picnic is scheduled for June 5th every year.",
        "Quarterly maintenance for the east wing elevator is due next month.",
        "The office recycling program starts on the first Monday of each quarter.",
        "Parking permits must be renewed annually at the front desk.",
    ]
    for i, text in enumerate(filler_texts):
        filler_pdf = tmp_path / f"filler_{i}.pdf"
        make_pdf(str(filler_pdf), text)
        ingest_document(str(filler_pdf))

    query = "What is the zzqorbnex status?"
    query_embedding = embed([query])[0]

    result = _select_documents(query, query_embedding, acting_role=None, limit=3)

    assert result[0] == bm25_id


def test_select_documents_excludes_restricted_documents_for_a_non_matching_role(tmp_path):
    public_pdf = tmp_path / "public.pdf"
    make_pdf(str(public_pdf), "The company picnic is scheduled for June 5th every year.")
    restricted_pdf = tmp_path / "restricted.pdf"
    make_pdf(str(restricted_pdf), "The confidential internal budget code is ZX-9942.")

    ingest_document(str(public_pdf))
    restricted_id = ingest_document(str(restricted_pdf), acl_group="finance")

    query = "What is the confidential internal budget code?"
    query_embedding = embed([query])[0]

    no_role = _select_documents(query, query_embedding, acting_role=None, limit=3)
    assert restricted_id not in no_role

    right_role = _select_documents(query, query_embedding, acting_role="finance", limit=3)
    assert restricted_id in right_role


def test_retrieve_only_returns_candidates_from_selected_documents(monkeypatch, tmp_path):
    keep_pdf = tmp_path / "keep.pdf"
    make_pdf(str(keep_pdf), "PTT committed to achieving Net Zero greenhouse gas emissions by the year 2050.")
    excluded_pdf = tmp_path / "excluded.pdf"
    make_pdf(str(excluded_pdf), "The company picnic is scheduled for June 5th every year.")

    keep_id = ingest_document(str(keep_pdf))
    ingest_document(str(excluded_pdf))

    keep_node_ids = node_ids_for(keep_id)

    def fake_select_documents(query, query_embedding, acting_role, limit):
        return [keep_id]

    monkeypatch.setattr(query_module, "_select_documents", fake_select_documents)

    query = "What year does PTT aim to achieve Net Zero emissions?"
    query_embedding = embed([query])[0]
    result = query_module._retrieve(query, query_embedding)

    result_node_ids = {node_id for node_id, _ in result}
    assert result_node_ids <= keep_node_ids
