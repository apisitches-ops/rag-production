# rag-production

## Running locally

Prerequisites: Docker Desktop, and API keys for [Voyage AI](https://dash.voyageai.com) (embedding + reranking) and [Gemini](https://aistudio.google.com) (generation + the eval harness's Ragas judge), exported as `VOYAGE_API_KEY` and `GEMINI_API_KEY` — see [ADR-0006](docs/adr/0006-cloud-hosted-embedding-rerank-generation.md) for why this is a fully cloud-hosted stack now (no local model, no Ollama).

```
docker compose up -d --wait
```

This starts Postgres (with the pgvector extension and schema already set up via `docker/postgres/init.sql`, run automatically on first startup against an empty volume — there's no migration tooling yet, so a schema change means `docker compose down -v` to pick it up on an existing volume) and Redis (the exact-match query cache, `app/cache.py` — optional at runtime: if it's unreachable, `/query` still works, just without caching).

Then, with a Python 3.11+ virtualenv:

```
pip install -e ".[dev]"
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag VOYAGE_API_KEY=... GEMINI_API_KEY=... uvicorn app.main:app --reload
```

`GET /health` returns `{"status": "ok"}` once it has verified a live connection to Postgres.

`POST /documents` (multipart, field `file`) ingests one PDF: extracts text (PyMuPDF), chunks it into parent/child Nodes (LlamaIndex `HierarchicalNodeParser`), embeds the child Nodes (`app/embeddings.py`, Voyage AI's `voyage-3`), and stores them in Postgres. Returns `{"document_id": <id>}`.

`POST /query` (JSON body `{"query": "...", "acting_role": "..."}`, `acting_role` optional) first checks the Redis exact-match cache (`app/cache.py`, keyed on the exact `query`+`acting_role` pair; a miss or an unreachable Redis both fall through to a real run) — on a miss it embeds the question, retrieves a wide candidate pool of child Nodes (each already expanded to its parent's wider content, and filtered to Documents whose `acl_group` is either unset or matches `acting_role`) by fusing dense (pgvector) and lexical (BM25, via LlamaIndex's `QueryFusionRetriever`, rebuilt fresh from Postgres each query) retrieval with Reciprocal Rank Fusion, dedupes by parent, reranks the deduped pool with Voyage AI's hosted `rerank-2` (`app/reranker.py`), keeps the top 5 by rerank score, asks the Generator (`app/generator.py`, Gemini) to answer from that context only via structured JSON output, and caches the result. Returns `{"answer": str, "citations": [node_id, ...], "contexts": [str, ...], "abstained": bool}` — `contexts` is the actual text handed to the Generator (used by the eval harness so it scores against what the pipeline really saw, not a re-derived approximation); `abstained` is `true` when the context wasn't enough to answer, signalled directly in the Generator's structured output.

## Tests

Requires Postgres running (see above) and `VOYAGE_API_KEY`/`GEMINI_API_KEY` set — any test touching ingestion, `/query`, `answer_query`, or `run_eval` calls the real Voyage AI/Gemini APIs, no mocking, consistent with how this test suite exercises real dependencies rather than doubles. This means running the suite costs a small amount of real API spend and is subject to those accounts' rate limits (the dev account here is capped at 3 RPM on Voyage AI, absent a payment method — expect a full run to take tens of minutes, not the seconds a purely local/mocked suite would):

```
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag VOYAGE_API_KEY=... GEMINI_API_KEY=... pytest
```

## Eval

`eval/golden_set.json` + `eval/corpus/` (51 Documents, 200 questions derived from the Kaggle Financial/Legal Evaluation Dataset — see `eval/build_corpus.py`) is the Golden Set. Running it wipes and re-ingests the database, so don't run it against data you want to keep:

```
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag VOYAGE_API_KEY=... GEMINI_API_KEY=... python -m eval.run_eval
```

Scores faithfulness, answer relevancy, and context precision (Ragas, judged by the same Generator/Embedder as the pipeline itself — Gemini + Voyage AI) for every answered question, and tracks a separate abstention rate/correctness rate for questions the pipeline should or shouldn't have Abstained on. A report is written to `eval/reports/<timestamp>.json`.
