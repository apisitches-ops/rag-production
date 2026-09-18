import app.query as query_module
from app.ingest import ingest_document

FIXTURE_DIR = "tests/fixtures/rerank_wiring"


def test_retrieve_over_fetches_then_reranks_before_truncating_to_top_k(monkeypatch):
    document_ids = [
        ingest_document(f"{FIXTURE_DIR}/doc_{i}.pdf", document_name=f"doc_{i}") for i in range(6)
    ]

    captured: dict = {}

    def fake_rerank(query: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
        captured["query"] = query
        captured["candidates"] = candidates
        return list(reversed(candidates))

    monkeypatch.setattr(query_module, "rerank", fake_rerank)
    # This test is about the over-fetch-then-rerank wiring specifically, not
    # document-scoped retrieval (tests/test_document_scoped_retrieval.py owns
    # that) — bypass Document narrowing so all 6 single-Node fixture Documents
    # stay in the candidate pool, matching what this test was written to check.
    monkeypatch.setattr(
        query_module,
        "_select_documents",
        lambda query, query_embedding, acting_role, limit: document_ids,
    )

    query_embedding = query_module.embed(["placeholder query"])[0]
    result = query_module._retrieve("placeholder query", query_embedding)

    # rerank() was called with more candidates than the final TOP_K, proving
    # _retrieve over-fetches instead of truncating to TOP_K before reranking
    assert len(captured["candidates"]) > query_module.TOP_K
    assert captured["query"] == "placeholder query"

    # the final result reflects the (fake, reversed) rerank order, truncated
    # to TOP_K — not the original dense-similarity order
    assert result == captured["candidates"][::-1][: query_module.TOP_K]
    assert len(result) == query_module.TOP_K
