import httpx
import psycopg
import pymupdf
from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes
from pgvector.psycopg import register_vector

from app import db

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "bge-m3"
EMBED_BATCH_SIZE = 32


def _extract_text(path: str) -> str:
    with pymupdf.open(path) as pdf:
        return "\n\n".join(page.get_text() for page in pdf)


def _embed(texts: list[str]) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        response = httpx.post(
            OLLAMA_EMBED_URL, json={"model": EMBED_MODEL, "input": batch}, timeout=60
        )
        response.raise_for_status()
        embeddings.extend(response.json()["embeddings"])
    return embeddings


def ingest_document(path: str, document_name: str | None = None) -> int:
    text = _extract_text(path)
    document = LlamaDocument(text=text)
    parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[512, 128])
    nodes = parser.get_nodes_from_documents([document])

    leaf_ids = {node.node_id for node in get_leaf_nodes(nodes)}
    child_nodes = [node for node in nodes if node.node_id in leaf_ids]
    embeddings = _embed([node.get_content() for node in child_nodes])
    embedding_by_id = dict(zip((node.node_id for node in child_nodes), embeddings))

    with psycopg.connect(db.DATABASE_URL) as conn:
        register_vector(conn)

        row = conn.execute(
            "INSERT INTO documents (path) VALUES (%s) RETURNING id",
            (document_name or path,),
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
