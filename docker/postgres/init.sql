CREATE EXTENSION IF NOT EXISTS vector;

-- init.sql only runs via docker-entrypoint-initdb.d against a first-time
-- (empty) data directory — it never re-runs against an existing volume, so
-- a schema change here can't reach a volume that predates it no matter how
-- this file is written. Run `docker compose down -v` to pick up schema
-- changes on an existing dev volume (see README).
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    path TEXT NOT NULL,
    acl_group TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    parent_id TEXT REFERENCES nodes(id),
    next_id TEXT REFERENCES nodes(id),
    content TEXT NOT NULL,
    embedding vector(1024)
);
