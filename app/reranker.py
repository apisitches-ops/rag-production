import threading
from typing import Any

import voyageai

# Unlike the self-hosted ONNX build this replaces (pinned to a specific Hub
# commit), Voyage's hosted rerank models aren't exposed as pinnable
# snapshots — this name always resolves to Voyage's current "rerank-2".
RERANK_MODEL = "rerank-2"

_lock = threading.Lock()
_client: Any = None


def _get_client() -> voyageai.Client:
    global _client
    with _lock:
        if _client is None:
            _client = voyageai.Client()
    return _client


def rerank(query: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not candidates:
        return []

    client = _get_client()
    contents = [content for _, content in candidates]
    result = client.rerank(query, contents, model=RERANK_MODEL)

    ranked = sorted(result.results, key=lambda r: -r.relevance_score)
    return [candidates[r.index] for r in ranked]
