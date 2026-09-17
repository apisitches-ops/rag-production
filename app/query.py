import httpx
import psycopg
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.llms.ollama import Ollama
from llama_index.retrievers.bm25 import BM25Retriever
from pgvector.psycopg import register_vector
from typing_extensions import TypedDict

from app import db
from app.ollama import embed
from app.reranker import rerank

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
DEV_GENERATOR_MODEL = "llama3.1:8b"
TOP_K = 5
# Over-fetch a wider dense-similarity pool than TOP_K so the reranker has
# real candidates to reorder, instead of TOP_K being decided by dense
# similarity alone.
RETRIEVAL_CANDIDATES = 20
assert RETRIEVAL_CANDIDATES > TOP_K, "over-fetch pool must be wider than the final top-K"

# Dev Generator (llama3.1:8b) can't reliably produce structured output, so
# Abstention is signalled with a plain-text marker instead of JSON. Switch to
# structured JSON output when the Validation Generator (Claude) is wired in
# — see ADR-0004.
ABSTENTION_MARKER = "INSUFFICIENT_CONTEXT"
ABSTENTION_MESSAGE = "The available context doesn't contain enough information to answer this question."


class Answer(TypedDict):
    answer: str
    citations: list[str]
    contexts: list[str]
    abstained: bool


# Shared by both retrievers below so a future change to candidate selection
# (e.g. an additional filter) can't drift between the two.
_CANDIDATE_JOIN_SQL = """
    FROM nodes n
    LEFT JOIN nodes p ON p.id = n.parent_id
    WHERE n.embedding IS NOT NULL
"""


def _text_node(node_id: str, group_key: str, content: str) -> TextNode:
    # group_key is plumbing (dedup-by-parent), not real content — excluded
    # from what BM25/embeddings actually index, otherwise its UUID pollutes
    # the lexical index with spurious hex-like token matches.
    return TextNode(
        id_=node_id,
        text=content,
        metadata={"group_key": group_key},
        excluded_embed_metadata_keys=["group_key"],
        excluded_llm_metadata_keys=["group_key"],
    )


class _DenseRetriever(BaseRetriever):
    def __init__(self, conn: psycopg.Connection, query_embedding: list[float], limit: int) -> None:
        self._conn = conn
        self._query_embedding = query_embedding
        self._limit = limit
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        rows = self._conn.execute(
            f"""
            SELECT n.id, COALESCE(n.parent_id, n.id), COALESCE(p.content, n.content),
                   n.embedding <=> %s::vector AS dist
            {_CANDIDATE_JOIN_SQL}
            ORDER BY dist
            LIMIT %s
            """,
            (self._query_embedding, self._limit),
        ).fetchall()
        return [
            NodeWithScore(node=_text_node(node_id, group_key, content), score=1 - dist)
            for node_id, group_key, content, dist in rows
        ]


def _bm25_retriever(conn: psycopg.Connection, limit: int) -> BM25Retriever | None:
    rows = conn.execute(
        f"SELECT n.id, COALESCE(n.parent_id, n.id), COALESCE(p.content, n.content) {_CANDIDATE_JOIN_SQL}"
    ).fetchall()
    if not rows:
        return None
    nodes = [_text_node(node_id, group_key, content) for node_id, group_key, content in rows]
    return BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=limit)


def _fused_retrieve(
    query: str, query_embedding: list[float], limit: int
) -> list[tuple[str, str, str]]:
    with psycopg.connect(db.DATABASE_URL) as conn:
        register_vector(conn)
        bm25 = _bm25_retriever(conn, limit)
        if bm25 is None:
            return []

        dense = _DenseRetriever(conn, query_embedding, limit)
        # num_queries=1 disables QueryFusionRetriever's default LLM-based
        # query expansion, so this adds no extra Dev Generator call. The
        # `llm` param is still required (unused here) — omitting it makes the
        # constructor eagerly resolve a default OpenAI LLM, which fails in
        # this environment. use_async=False: neither retriever is actually
        # async, so the default async fan-out just adds event-loop overhead
        # for no concurrency benefit.
        fusion = QueryFusionRetriever(
            [dense, bm25],
            llm=Ollama(model=DEV_GENERATOR_MODEL, request_timeout=120),
            mode=FUSION_MODES.RECIPROCAL_RANK,
            similarity_top_k=limit,
            num_queries=1,
            use_async=False,
        )
        results = fusion.retrieve(query)

    return [
        (r.node.node_id, r.node.metadata["group_key"], r.node.get_content()) for r in results
    ]


def _retrieve(query: str, query_embedding: list[float]) -> list[tuple[str, str]]:
    rows = _fused_retrieve(query, query_embedding, RETRIEVAL_CANDIDATES)

    seen_groups: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for node_id, group_key, content in rows:
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        candidates.append((node_id, content))

    return rerank(query, candidates)[:TOP_K]


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
    contexts = _retrieve(query, query_embedding)
    generated = _generate(query, contexts)

    if ABSTENTION_MARKER in generated:
        return Answer(answer=ABSTENTION_MESSAGE, citations=[], contexts=[], abstained=True)

    return Answer(
        answer=generated,
        citations=[node_id for node_id, _ in contexts],
        contexts=[content for _, content in contexts],
        abstained=False,
    )
