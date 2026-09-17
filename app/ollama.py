import httpx

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "bge-m3"
EMBED_BATCH_SIZE = 32


def embed(texts: list[str]) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        response = httpx.post(
            OLLAMA_EMBED_URL, json={"model": EMBED_MODEL, "input": batch}, timeout=60
        )
        response.raise_for_status()
        embeddings.extend(response.json()["embeddings"])
    return embeddings
