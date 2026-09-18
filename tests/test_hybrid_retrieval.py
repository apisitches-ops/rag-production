from llama_index.core.schema import MetadataMode

import app.query as query_module
from app.ingest import ingest_document
from app.ollama import embed
from conftest import make_pdf as _make_pdf


def test_fused_retrieve_combines_dense_and_bm25_signals(tmp_path):
    # No semantic content in common with a real query, but shares a rare
    # literal token with it — dense similarity alone shouldn't rank this
    # well, but BM25 (exact term match) should rank it very highly.
    bm25_pdf = tmp_path / "bm25.pdf"
    _make_pdf(str(bm25_pdf), "zzqorbnex zzqorbnex zzqorbnex flibbertigibbet wombatronic filler text.")

    # Real semantic content a dense embedding should find easily, no shared
    # literal tokens with the query below.
    dense_pdf = tmp_path / "dense.pdf"
    _make_pdf(
        str(dense_pdf),
        "PTT committed to achieving Net Zero greenhouse gas emissions by the year 2050.",
    )

    ingest_document(str(bm25_pdf), document_name="bm25_doc")
    ingest_document(str(dense_pdf), document_name="dense_doc")

    query = "What is the zzqorbnex status, and what is the Net Zero commitment year?"
    query_embedding = embed([query])[0]

    result = query_module._fused_retrieve(query, query_embedding, limit=5)

    contents = [content for _, _, content in result]
    assert any("zzqorbnex" in c for c in contents), "BM25 signal (exact rare-term match) was dropped"
    assert any("Net Zero" in c for c in contents), "Dense signal (semantic match) was dropped"
    assert len(result) <= 5


def test_fused_retrieve_returns_empty_when_corpus_is_empty():
    query = "anything"
    query_embedding = embed([query])[0]

    result = query_module._fused_retrieve(query, query_embedding, limit=5)

    assert result == []


def test_text_node_excludes_group_key_from_indexed_content():
    # group_key (a parent-Node UUID) is plumbing for dedup, not real content.
    # If it isn't excluded, its hex-like characters can spuriously match a
    # query term that happens to overlap with part of the UUID.
    node = query_module._text_node(
        "child-1", "deadbeef-face-4a3b-9c1d-0011deadface", "The quick brown fox."
    )

    assert "deadbeef" not in node.get_content(metadata_mode=MetadataMode.EMBED)
    assert "deadbeef" not in node.get_content(metadata_mode=MetadataMode.LLM)
