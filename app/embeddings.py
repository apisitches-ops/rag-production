import threading
from typing import Any, cast

import voyageai

EMBED_MODEL = "voyage-3"
EMBED_BATCH_SIZE = 32

_lock = threading.Lock()
_client: Any = None


def _get_client() -> voyageai.Client:
    global _client
    with _lock:
        if _client is None:
            # max_retries: the SDK's own built-in exponential backoff (via
            # tenacity), retrying on RateLimitError/ServiceUnavailableError/
            # Timeout — needed since an account without a payment method on
            # file is capped at 3 RPM. Lazy singleton (matching
            # app/reranker.py's pattern) so a missing VOYAGE_API_KEY fails on
            # the first real request, not at import time / app startup.
            _client = voyageai.Client(max_retries=10)
    return _client


def embed(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        result = client.embed(batch, model=EMBED_MODEL)
        # output_dtype is never passed above, so this is always float — the
        # SDK's return type is a broader union to also cover int8/binary
        # dtypes we don't use.
        embeddings.extend(cast(list[list[float]], result.embeddings))
    return embeddings
