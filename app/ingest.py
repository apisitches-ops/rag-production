import csv
from pathlib import Path

import psycopg
import pymupdf
from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes
from pgvector.psycopg import register_vector

from app import db
from app.ollama import embed


def _extract_pdf_text(path: str) -> str:
    with pymupdf.open(path) as pdf:
        return "\n\n".join(page.get_text() for page in pdf)


def _extract_csv_text(path: str) -> str:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    # A single-column CSV *is* its content — prefixing every line with the
    # column name (e.g. "content: ...") would silently drift the indexed
    # text away from the source, which matters for eval/build_corpus.py's
    # one-column-per-Document format specifically.
    if len(fieldnames) == 1:
        (only_key,) = fieldnames
        return "\n".join(row[only_key] for row in rows if row.get(only_key))

    lines = [
        ", ".join(f"{key}: {value}" for key, value in row.items() if key is not None and value)
        for row in rows
    ]
    return "\n".join(line for line in lines if line)


_EXTRACTORS = {".pdf": _extract_pdf_text, ".csv": _extract_csv_text}


def _extract_text(path: str) -> str:
    extractor = _EXTRACTORS.get(Path(path).suffix.lower())
    if extractor is None:
        raise ValueError(f"unsupported file extension: {path}")
    return extractor(path)


def ingest_document(
    path: str, document_name: str | None = None, acl_group: str | None = None
) -> int:
    text = _extract_text(path)
    document = LlamaDocument(text=text)
    parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[512, 128])
    nodes = parser.get_nodes_from_documents([document])

    leaf_ids = {node.node_id for node in get_leaf_nodes(nodes)}
    child_nodes = [node for node in nodes if node.node_id in leaf_ids]
    embeddings = embed([node.get_content() for node in child_nodes])
    embedding_by_id = dict(zip((node.node_id for node in child_nodes), embeddings))

    with psycopg.connect(db.DATABASE_URL) as conn:
        register_vector(conn)

        row = conn.execute(
            "INSERT INTO documents (path, acl_group) VALUES (%s, %s) RETURNING id",
            (document_name or path, acl_group),
        ).fetchone()
        assert row is not None
        document_id: int = row[0]

        conn.cursor().executemany(
            "INSERT INTO nodes (id, document_id, content, embedding) VALUES (%s, %s, %s, %s)",
            [
                (
                    node.node_id,
                    document_id,
                    node.get_content(),
                    embedding_by_id.get(node.node_id),
                )
                for node in nodes
            ],
        )

        conn.cursor().executemany(
            "UPDATE nodes SET parent_id = %s WHERE id = %s",
            [
                (node.parent_node.node_id, node.node_id)
                for node in nodes
                if node.parent_node is not None
            ],
        )

    return document_id
