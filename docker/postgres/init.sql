CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    path TEXT NOT NULL
);

-- init.sql only runs via docker-entrypoint-initdb.d on a first-time (empty)
-- data directory, so CREATE TABLE IF NOT EXISTS above is a no-op against any
-- volume that predates this column. This keeps existing volumes in sync too.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS acl_group TEXT;

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    parent_id TEXT REFERENCES nodes(id),
    content TEXT NOT NULL,
    embedding vector(1024)
);
