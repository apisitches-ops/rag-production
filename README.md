# rag-production

## Running locally

Prerequisites: Docker Desktop, and [Ollama](https://ollama.com) running natively on the host with `bge-m3` pulled (`ollama pull bge-m3`) — not containerized, so it keeps Metal GPU acceleration (see ADR-0003).

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

## Tests

Requires Postgres running (see above):

```
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag pytest
```
