import os
from typing import TypedDict

import psycopg

from app import cache

DATABASE_URL = os.environ["DATABASE_URL"]


class DocumentSummary(TypedDict):
    id: int
    name: str
    acl_group: str | None
    node_ids: list[str]


def check_connection() -> None:
    with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
        conn.execute("SELECT 1")


def reset() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("TRUNCATE nodes, documents RESTART IDENTITY CASCADE")
    cache.clear()


def list_documents() -> list[DocumentSummary]:
    with psycopg.connect(DATABASE_URL) as conn:
        documents = conn.execute(
            "SELECT id, path, acl_group FROM documents ORDER BY id"
        ).fetchall()
        nodes = conn.execute("SELECT id, document_id FROM nodes").fetchall()

    node_ids_by_document: dict[int, list[str]] = {}
    for node_id, document_id in nodes:
        node_ids_by_document.setdefault(document_id, []).append(node_id)

    return [
        DocumentSummary(
            id=document_id,
            name=path,
            acl_group=acl_group,
            node_ids=node_ids_by_document.get(document_id, []),
        )
        for document_id, path, acl_group in documents
    ]
