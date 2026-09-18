import redis

import app.query as query_module
from app import cache, db
from app.ingest import ingest_document
from app.query import answer_query
from conftest import make_pdf

FIXTURE_PDF = "tests/fixtures/sample.pdf"


def test_answer_query_returns_cached_result_without_rerunning_pipeline(monkeypatch):
    ingest_document(FIXTURE_PDF)

    call_count = 0
    original_generate = query_module._generate

    def counting_generate(query, contexts):
        nonlocal call_count
        call_count += 1
        return original_generate(query, contexts)

    monkeypatch.setattr(query_module, "_generate", counting_generate)

    query = "What year does PTT aim to achieve Net Zero emissions?"
    first = answer_query(query)
    second = answer_query(query)

    assert call_count == 1, "expected the second call to be served from cache, not re-run"
    assert first == second


def test_answer_query_cache_is_isolated_by_acting_role(monkeypatch):
    ingest_document(FIXTURE_PDF)

    call_count = 0
    original_generate = query_module._generate

    def counting_generate(query, contexts):
        nonlocal call_count
        call_count += 1
        return original_generate(query, contexts)

    monkeypatch.setattr(query_module, "_generate", counting_generate)

    query = "What year does PTT aim to achieve Net Zero emissions?"
    answer_query(query)
    answer_query(query, acting_role="finance")

    assert call_count == 2, "a different acting_role must not be served from another role's cache entry"


def test_answer_query_falls_back_when_redis_is_unreachable(monkeypatch):
    ingest_document(FIXTURE_PDF)

    unreachable_client = redis.Redis(host="localhost", port=1, socket_connect_timeout=0.5)
    monkeypatch.setattr(cache, "_client", unreachable_client)

    result = answer_query("What year does PTT aim to achieve Net Zero emissions?")

    assert result["abstained"] is False
    assert "2050" in result["answer"]


def test_ingest_document_flushes_the_cache(tmp_path):
    unrelated_pdf = tmp_path / "unrelated.pdf"
    make_pdf(str(unrelated_pdf), "The company picnic is scheduled for June 5th every year.")
    ingest_document(str(unrelated_pdf))

    query = "What is the secret code?"
    first = answer_query(query)
    assert first["abstained"] is True

    answer_pdf = tmp_path / "answer.pdf"
    make_pdf(str(answer_pdf), "The secret code is GAMMA789.")
    ingest_document(str(answer_pdf))

    second = answer_query(query)
    assert second["abstained"] is False
    assert "GAMMA789" in second["answer"]


def test_db_reset_flushes_the_cache(tmp_path):
    first_pdf = tmp_path / "first.pdf"
    make_pdf(str(first_pdf), "The secret code is ALPHA123.")
    ingest_document(str(first_pdf))

    query = "What is the secret code?"
    first = answer_query(query)
    assert "ALPHA123" in first["answer"]

    db.reset()

    second_pdf = tmp_path / "second.pdf"
    make_pdf(str(second_pdf), "The secret code is BETA456.")
    ingest_document(str(second_pdf))

    second = answer_query(query)
    assert "BETA456" in second["answer"]
    assert "ALPHA123" not in second["answer"]
