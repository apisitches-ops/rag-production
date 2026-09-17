# rag-production

## Running locally

Prerequisites: Docker Desktop, and [Ollama](https://ollama.com) running natively on the host with `bge-m3` pulled (`ollama pull bge-m3`) — not containerized, so it keeps Metal GPU acceleration (see ADR-0003).

```
docker compose up -d --wait
```

This starts Postgres with the pgvector extension already enabled (via `docker/postgres/init.sql`, run automatically on first startup).

Then, with a Python 3.11+ virtualenv:

```
pip install -e ".[dev]"
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag uvicorn app.main:app --reload
```

`GET /health` returns `{"status": "ok"}` once it has verified a live connection to Postgres.

## Tests

Requires Postgres running (see above):

```
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag pytest
```
