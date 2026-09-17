CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    parent_id TEXT REFERENCES nodes(id),
    content TEXT NOT NULL,
    embedding vector(1024)
);
