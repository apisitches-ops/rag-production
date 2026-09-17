import threading
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download
from optimum.onnxruntime import ORTModelForSequenceClassification
from transformers import AutoTokenizer

# ONNX build of BGE-reranker-v2-m3, chosen so this runs on CPU/Metal without a
# GPU, per ADR-0003. Pinned to a specific commit, like every other dependency
# in this repo, since the Hub repo has no version tags.
MODEL_ID = "EmbeddedLLM/bge-reranker-v2-m3-onnx-o3-cpu"
MODEL_REVISION = "c46cc14e4748b2332899dfe5e6dcd4750caf7cef"
LOCAL_MODEL_DIR = Path(__file__).resolve().parent.parent / ".cache" / "reranker-onnx"
RERANK_BATCH_SIZE = 16

_lock = threading.Lock()
_tokenizer: Any = None
_model: Any = None


def _model_is_cached() -> bool:
    return (LOCAL_MODEL_DIR / "model.onnx.data").exists()


def _load() -> tuple[Any, Any]:
    global _tokenizer, _model
    with _lock:
        if _model is None or _tokenizer is None:
            if not _model_is_cached():
                # The default HF cache uses symlinks that onnxruntime's
                # external-data loader rejects; downloading to a real
                # directory avoids that.
                snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=LOCAL_MODEL_DIR)
            _tokenizer = AutoTokenizer.from_pretrained(LOCAL_MODEL_DIR)
            _model = ORTModelForSequenceClassification.from_pretrained(LOCAL_MODEL_DIR)
    return _tokenizer, _model


def warm_up() -> None:
    """Load the model eagerly (e.g. at app startup) so the first real
    request doesn't block on a multi-GB download."""
    _load()


def rerank(query: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not candidates:
        return []

    tokenizer, model = _load()
    scores: list[float] = []
    for start in range(0, len(candidates), RERANK_BATCH_SIZE):
        batch = candidates[start : start + RERANK_BATCH_SIZE]
        contents = [content for _, content in batch]
        inputs = tokenizer(
            [query] * len(contents), contents, padding=True, truncation=True, return_tensors="pt"
        )
        outputs = model(**inputs)
        scores.extend(outputs.logits.squeeze(-1).tolist())

    ranked = sorted(zip(candidates, scores), key=lambda pair: -pair[1])
    return [candidate for candidate, _ in ranked]
