# rag-production

## Running locally

Prerequisites: Docker Desktop, and [Ollama](https://ollama.com) running natively on the host with `bge-m3` and `llama3.1:8b` (the Dev Generator, see ADR-0004) pulled — not containerized, so it keeps Metal GPU acceleration (see ADR-0003).

```
docker compose up -d --wait
```

This starts Postgres with the pgvector extension and schema already set up (via `docker/postgres/init.sql`, run automatically on first startup against an empty volume — there's no migration tooling yet, so a schema change means `docker compose down -v` to pick it up on an existing volume).

Then, with a Python 3.11+ virtualenv:

```
pip install -e ".[dev]"
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag uvicorn app.main:app --reload
```

`GET /health` returns `{"status": "ok"}` once it has verified a live connection to Postgres.

`POST /documents` (multipart, field `file`) ingests one PDF: extracts text (PyMuPDF), chunks it into parent/child Nodes (LlamaIndex `HierarchicalNodeParser`), embeds the child Nodes (`bge-m3` via Ollama), and stores them in Postgres. Returns `{"document_id": <id>}`.

`POST /query` (JSON body `{"query": "..."}`) retrieves the closest child Nodes by embedding similarity, expands each to its parent's wider content, and asks the Dev Generator (`llama3.1:8b`) to answer from that context only. Returns `{"answer": str, "citations": [node_id, ...], "contexts": [str, ...], "abstained": bool}` — `contexts` is the actual text handed to the Generator (used by the eval harness so it scores against what the pipeline really saw, not a re-derived approximation); `abstained` is `true` when the context wasn't enough to answer, per ADR-0004.

A reranker (`app/reranker.py`, BGE-reranker-v2-m3 ONNX-quantized, per ADR-0003) is available but not yet wired into `/query` — that's a separate ticket. First use downloads the model (~2.3GB) into `.cache/reranker-onnx/` (gitignored); later calls reuse it.

## Tests

Requires Postgres running (see above):

```
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag pytest
```

## Eval

`eval/golden_set.json` + `eval/corpus/` (51 Documents, 200 questions derived from the Kaggle Financial/Legal Evaluation Dataset — see `eval/build_corpus.py`) is the Golden Set. Running it wipes and re-ingests the database, so don't run it against data you want to keep:

```
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag python -m eval.run_eval
```

Scores faithfulness, answer relevancy, and context precision (Ragas, judged by the Dev Generator per ADR-0004) for every answered question, and tracks a separate abstention rate/correctness rate for questions the pipeline should or shouldn't have Abstained on. `faithfulness` is frequently `null` — the Dev Generator (llama3.1:8b) can't reliably produce the structured claim-verification output Ragas needs for that specific metric; see ADR-0004. A report is written to `eval/reports/<timestamp>.json`.
