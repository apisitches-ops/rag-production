import threading
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel

# Chosen by the user for this migration (replaces the local llama3.1:8b Dev
# Generator, ticket #26) — a low-cost/low-latency Gemini tier, matching the
# Dev Generator's original cost/iteration-speed rationale (ADR-0004).
GENERATOR_MODEL = "gemini-3.1-flash-lite"

_lock = threading.Lock()
_client: Any = None


class _GeneratedAnswer(BaseModel):
    answer: str
    abstained: bool


def _get_client() -> genai.Client:
    global _client
    with _lock:
        if _client is None:
            _client = genai.Client()
    return _client


def generate(query: str, contexts: list[tuple[str, str]]) -> tuple[str, bool]:
    context_block = "\n\n".join(content for _, content in contexts)
    prompt = (
        "Answer the question using ONLY the context below. Be concise. "
        "If the context does not contain enough information to answer, "
        "set abstained to true.\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {query}\nAnswer:"
    )
    client = _get_client()
    response = client.models.generate_content(
        model=GENERATOR_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_GeneratedAnswer,
            temperature=0,
        ),
    )
    parsed = response.parsed
    assert isinstance(
        parsed, _GeneratedAnswer
    ), f"Gemini did not return parseable structured output: {response.text!r}"
    return parsed.answer, parsed.abstained
