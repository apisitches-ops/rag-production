import httpx
import psycopg
from pgvector.psycopg import register_vector
from typing_extensions import TypedDict

from app import db
from app.ollama import embed

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
DEV_GENERATOR_MODEL = "llama3.1:8b"
TOP_K = 5

# Dev Generator (llama3.1:8b) can't reliably produce structured output, so
# Abstention is signalled with a plain-text marker instead of JSON. Switch to
# structured JSON output when the Validation Generator (Claude) is wired in
# — see ADR-0004.
ABSTENTION_MARKER = "INSUFFICIENT_CONTEXT"
ABSTENTION_MESSAGE = "The available context doesn't contain enough information to answer this question."


class Answer(TypedDict):
    answer: str
    citations: list[str]
    abstained: bool


def _retrieve(query_embedding: list[float]) -> list[tuple[str, str]]:
    with psycopg.connect(db.DATABASE_URL) as conn:
        register_vector(conn)
        rows = conn.execute(
            """
            SELECT n.id, COALESCE(n.parent_id, n.id), COALESCE(p.content, n.content)
            FROM nodes n
            LEFT JOIN nodes p ON p.id = n.parent_id
            WHERE n.embedding IS NOT NULL
            ORDER BY n.embedding <=> %s::vector
            LIMIT %s
            """,
            (query_embedding, TOP_K),
        ).fetchall()

    seen_groups: set[str] = set()
    contexts: list[tuple[str, str]] = []
    for node_id, group_key, content in rows:
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        contexts.append((node_id, content))
    return contexts


def _generate(query: str, contexts: list[tuple[str, str]]) -> str:
    context_block = "\n\n".join(content for _, content in contexts)
    prompt = (
        "Answer the question using ONLY the context below. Be concise. "
        f"If the context does not contain enough information to answer, respond with exactly: {ABSTENTION_MARKER}\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {query}\nAnswer:"
    )
    response = httpx.post(
        OLLAMA_GENERATE_URL,
        json={
            "model": DEV_GENERATOR_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=120,
    )
    response.raise_for_status()
    text: str = response.json()["response"]
    return text.strip()


def answer_query(query: str) -> Answer:
    query_embedding = embed([query])[0]
    contexts = _retrieve(query_embedding)
    generated = _generate(query, contexts)

    if ABSTENTION_MARKER in generated:
        return Answer(answer=ABSTENTION_MESSAGE, citations=[], abstained=True)

    return Answer(
        answer=generated,
        citations=[node_id for node_id, _ in contexts],
        abstained=False,
    )
