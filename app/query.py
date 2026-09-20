import re

import psycopg
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.llms.ollama import Ollama
from llama_index.retrievers.bm25 import BM25Retriever
from pgvector.psycopg import register_vector
from typing_extensions import TypedDict

from app import db
from app.cache import get_cached_answer, set_cached_answer
from app.embeddings import embed
from app.generator import generate
from app.reranker import rerank

# QueryFusionRetriever's constructor requires an `llm`, but num_queries=1
# below disables the only path that would actually call it (LLM-based query
# expansion) — this is never invoked, just a placeholder to satisfy the
# constructor without it eagerly resolving a default OpenAI LLM (which fails
# in this environment). Unrelated to app/generator.py's real Generator.
_FUSION_LLM_PLACEHOLDER_MODEL = "llama3.1:8b"
TOP_K = 5
# Over-fetch a wider dense-similarity pool than TOP_K so the reranker has
# real candidates to reorder, instead of TOP_K being decided by dense
# similarity alone.
RETRIEVAL_CANDIDATES = 20
assert RETRIEVAL_CANDIDATES > TOP_K, "over-fetch pool must be wider than the final top-K"
# Narrow to the top-N most relevant Documents before chunk-level retrieval,
# so a wrong-but-superficially-similar Document's Nodes never enter the
# candidate pool — see docs/progress-log.md 2026-09-18 (manual review: 89%
# of wrong answers were cross-document contamination).
DOCUMENT_CANDIDATES = 3

ABSTENTION_MESSAGE = "The available context doesn't contain enough information to answer this question."

# The confirmed real pattern (#31): a Node ending mid-number, a run of
# digits immediately followed by a decimal point with nothing after it.
# Deliberately narrow rather than a broad "incomplete sentence" heuristic,
# to keep false positives low.
_TRUNCATED_DECIMAL_RE = re.compile(r"\d\.\s*$")


def _looks_truncated(content: str) -> bool:
    return bool(_TRUNCATED_DECIMAL_RE.search(content))


class Answer(TypedDict):
    answer: str
    citations: list[str]
    contexts: list[str]
    abstained: bool


# Shared everywhere an ACL check happens (chunk-level and Document-level
# selection) so a future ACL rule change can't drift between the copies.
_ACL_FILTER_SQL = "(d.acl_group IS NULL OR d.acl_group = %s)"

# Shared by both retrievers below so a future change to candidate selection
# (e.g. an additional filter) can't drift between the two. ACL filtering
# happens here, not after retrieval, so a restricted Document's Nodes never
# even enter the candidate pool that fusion/rerank/dedup operate on.
_CANDIDATE_JOIN_SQL = f"""
    FROM nodes n
    LEFT JOIN nodes p ON p.id = n.parent_id
    JOIN documents d ON d.id = n.document_id
    WHERE n.embedding IS NOT NULL
      AND {_ACL_FILTER_SQL}
"""


def _document_filter(document_ids: list[int] | None) -> tuple[str, tuple]:
    if document_ids is None:
        return "", ()
    return " AND n.document_id = ANY(%s)", (document_ids,)


def _text_node(node_id: str, content: str, metadata: dict[str, str | int]) -> TextNode:
    # Metadata here is plumbing (dedup/ownership keys), not real content —
    # excluded from what BM25/embeddings actually index, otherwise it
    # pollutes the lexical index with spurious matches (e.g. a UUID or an
    # integer id matching part of a real query term).
    keys = list(metadata.keys())
    return TextNode(
        id_=node_id,
        text=content,
        metadata=metadata,
        excluded_embed_metadata_keys=keys,
        excluded_llm_metadata_keys=keys,
    )


class _DenseRetriever(BaseRetriever):
    def __init__(
        self,
        conn: psycopg.Connection,
        query_embedding: list[float],
        limit: int,
        acting_role: str | None,
        document_ids: list[int] | None = None,
    ) -> None:
        self._conn = conn
        self._query_embedding = query_embedding
        self._limit = limit
        self._acting_role = acting_role
        self._document_ids = document_ids
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        filter_sql, filter_params = _document_filter(self._document_ids)
        rows = self._conn.execute(
            f"""
            SELECT n.id, COALESCE(n.parent_id, n.id), COALESCE(p.content, n.content),
                   n.embedding <=> %s::vector AS dist
            {_CANDIDATE_JOIN_SQL}{filter_sql}
            ORDER BY dist
            LIMIT %s
            """,
            (self._query_embedding, self._acting_role, *filter_params, self._limit),
        ).fetchall()
        return [
            NodeWithScore(
                node=_text_node(node_id, content, {"group_key": group_key}), score=1 - dist
            )
            for node_id, group_key, content, dist in rows
        ]


def _bm25_retriever(
    conn: psycopg.Connection,
    limit: int,
    acting_role: str | None,
    document_ids: list[int] | None = None,
) -> BM25Retriever | None:
    filter_sql, filter_params = _document_filter(document_ids)
    rows = conn.execute(
        f"SELECT n.id, COALESCE(n.parent_id, n.id), COALESCE(p.content, n.content) {_CANDIDATE_JOIN_SQL}{filter_sql}",
        (acting_role, *filter_params),
    ).fetchall()
    if not rows:
        return None
    nodes = [
        _text_node(node_id, content, {"group_key": group_key})
        for node_id, group_key, content in rows
    ]
    return BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=limit)


def _fused_retrieve(
    query: str,
    query_embedding: list[float],
    limit: int,
    acting_role: str | None = None,
    document_ids: list[int] | None = None,
) -> list[tuple[str, str, str]]:
    with psycopg.connect(db.DATABASE_URL) as conn:
        register_vector(conn)
        bm25 = _bm25_retriever(conn, limit, acting_role, document_ids)
        if bm25 is None:
            return []

        dense = _DenseRetriever(conn, query_embedding, limit, acting_role, document_ids)
        # num_queries=1 disables QueryFusionRetriever's default LLM-based
        # query expansion, so this adds no extra Generator call. The
        # `llm` param is still required (unused here) — omitting it makes the
        # constructor eagerly resolve a default OpenAI LLM, which fails in
        # this environment. use_async=False: neither retriever is actually
        # async, so the default async fan-out just adds event-loop overhead
        # for no concurrency benefit.
        fusion = QueryFusionRetriever(
            [dense, bm25],
            llm=Ollama(model=_FUSION_LLM_PLACEHOLDER_MODEL, request_timeout=120),
            mode=FUSION_MODES.RECIPROCAL_RANK,
            similarity_top_k=limit,
            num_queries=1,
            use_async=False,
        )
        results = fusion.retrieve(query)

    return [
        (r.node.node_id, r.node.metadata["group_key"], r.node.get_content()) for r in results
    ]


def _select_documents(
    query: str, query_embedding: list[float], acting_role: str | None, limit: int
) -> list[int]:
    with psycopg.connect(db.DATABASE_URL) as conn:
        register_vector(conn)
        # Both queries below must see the same snapshot of nodes/documents —
        # otherwise a Document ingested between them could get a real BM25
        # rank but be missing from the dense ranking (or vice versa),
        # silently skewing its fused score instead of erroring. Must be the
        # first statement in the transaction.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")

        dense_rows = conn.execute(
            f"""
            SELECT n.document_id, MIN(n.embedding <=> %s::vector) AS dist
            FROM nodes n
            JOIN documents d ON d.id = n.document_id
            WHERE n.embedding IS NOT NULL
              AND {_ACL_FILTER_SQL}
            GROUP BY n.document_id
            ORDER BY dist
            """,
            (query_embedding, acting_role),
        ).fetchall()

        # COALESCE(p.content, n.content) — the same parent-expanded text
        # chunk-level BM25/dense retrieval actually searches (_CANDIDATE_JOIN_SQL)
        # — not just the child Node's own text, which can be too narrow to
        # carry a Document's distinguishing keywords.
        node_rows = conn.execute(
            f"""
            SELECT n.id, n.document_id, COALESCE(p.content, n.content)
            FROM nodes n
            LEFT JOIN nodes p ON p.id = n.parent_id
            JOIN documents d ON d.id = n.document_id
            WHERE n.embedding IS NOT NULL
              AND {_ACL_FILTER_SQL}
            """,
            (acting_role,),
        ).fetchall()

    if not node_rows:
        return []

    dense_rank = {document_id: rank for rank, (document_id, _) in enumerate(dense_rows)}

    nodes = [
        _text_node(node_id, content, {"document_id": document_id})
        for node_id, document_id, content in node_rows
    ]
    bm25_results = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=len(nodes)).retrieve(
        query
    )

    # Collapse node-level BM25 results to one best-matching-node-per-Document
    # (first occurrence = best rank) in BM25 order, then rank *within that
    # Document population* rather than using the raw node-level rank number.
    # Node-level ranks are drawn from a much larger population than
    # dense_rank's Document-level ranks, so reusing them directly would make
    # the lexical signal contribute far less than the dense one to the fused
    # score regardless of true relevance (found in code review, confirmed by
    # direct reproduction) — defeating the point of fusing the two signals.
    bm25_document_order: list[int] = []
    seen_documents: set[int] = set()
    for result in bm25_results:
        document_id = result.node.metadata["document_id"]
        if document_id not in seen_documents:
            seen_documents.add(document_id)
            bm25_document_order.append(document_id)
    bm25_rank = {document_id: rank for rank, document_id in enumerate(bm25_document_order)}

    # Reciprocal Rank Fusion (k=60, matching QueryFusionRetriever's own
    # default) implemented directly here rather than via QueryFusionRetriever
    # itself, which operates on Nodes — forcing it to rank Documents would
    # need a synthetic per-Document "Node" (all child content concatenated),
    # which changes what's being ranked (whole-document blob similarity)
    # away from the agreed design (the single best-matching child Node
    # represents its Document).
    #
    # dense_rows and node_rows share the exact same WHERE filter and are
    # read under REPEATABLE READ, so they cover the same Document set by
    # construction — every id in one ranking is guaranteed to be in the
    # other, no missing-rank fallback needed.
    k = 60.0
    scores = {
        document_id: 1.0 / (k + dense_rank[document_id]) + 1.0 / (k + bm25_rank[document_id])
        for document_id in dense_rank
    }

    ranked = sorted(dense_rank, key=lambda document_id: scores[document_id], reverse=True)
    return ranked[:limit]


def _merge_with_overlap(content: str, next_content: str) -> str:
    # chunk_overlap re-includes a run of trailing tokens from `content` at
    # the start of `next_content` — appending it bare would duplicate that
    # run. Find the longest suffix of `content` that's also a prefix of
    # `next_content` and only append what follows it.
    max_overlap = min(len(content), len(next_content))
    for overlap_len in range(max_overlap, 0, -1):
        if content[-overlap_len:] == next_content[:overlap_len]:
            return content + next_content[overlap_len:]
    return content + next_content


def _stitch_truncated_contents(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    truncated_ids = [node_id for node_id, content in candidates if _looks_truncated(content)]
    if not truncated_ids:
        return candidates

    # node_id is always the leaf Node's own id (see _DenseRetriever/
    # _bm25_retriever above), even when `content` was widened to its
    # parent's — so the real next-sibling Node is the parent's next_id when
    # a parent exists, falling back to the leaf's own next_id otherwise
    # (currently unreachable with chunk_sizes=[512, 128], since every Node
    # with an embedding has a parent — kept for whichever level actually
    # produced the displayed content, matching COALESCE(p.content,
    # n.content)'s own precedence). Single-hop: the appended Node's own
    # content is not itself checked for truncation.
    with psycopg.connect(db.DATABASE_URL) as conn:
        rows = conn.execute(
            """
            SELECT n.id, CASE WHEN n.parent_id IS NOT NULL THEN pn.content ELSE ln.content END
            FROM nodes n
            LEFT JOIN nodes p ON p.id = n.parent_id
            LEFT JOIN nodes pn ON pn.id = p.next_id
            LEFT JOIN nodes ln ON ln.id = n.next_id
            WHERE n.id = ANY(%s)
            """,
            (truncated_ids,),
        ).fetchall()
    next_content_by_id = {node_id: next_content for node_id, next_content in rows}

    result = []
    for node_id, content in candidates:
        next_content = next_content_by_id.get(node_id)
        # Only stitch when the candidate continuation actually looks like it
        # completes a number here — content ending in a bare decimal point
        # can otherwise be a complete sentence that just happens to end in
        # a whole number (e.g. "...fiscal year 2024."), and appending an
        # unrelated Node's content onto that would reintroduce the same
        # cross-excerpt contamination #33 already fixed at the prompt level.
        if next_content is not None and next_content.lstrip()[:1].isdigit():
            result.append((node_id, _merge_with_overlap(content, next_content)))
        else:
            result.append((node_id, content))
    return result


def _retrieve(
    query: str, query_embedding: list[float], acting_role: str | None = None
) -> list[tuple[str, str]]:
    document_ids = _select_documents(query, query_embedding, acting_role, DOCUMENT_CANDIDATES)
    rows = _fused_retrieve(
        query, query_embedding, RETRIEVAL_CANDIDATES, acting_role, document_ids
    )

    seen_groups: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for node_id, group_key, content in rows:
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        candidates.append((node_id, content))

    top = rerank(query, candidates)[:TOP_K]
    return _stitch_truncated_contents(top)


def answer_query(query: str, acting_role: str | None = None) -> Answer:
    cached = get_cached_answer(query, acting_role)
    if cached is not None:
        return cached

    query_embedding = embed([query])[0]
    contexts = _retrieve(query, query_embedding, acting_role)
    generated, abstained = generate(query, contexts)

    if abstained:
        answer = Answer(answer=ABSTENTION_MESSAGE, citations=[], contexts=[], abstained=True)
    else:
        answer = Answer(
            answer=generated,
            citations=[node_id for node_id, _ in contexts],
            contexts=[content for _, content in contexts],
            abstained=False,
        )

    set_cached_answer(query, acting_role, answer)
    return answer
