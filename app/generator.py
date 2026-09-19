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


def _build_prompt(query: str, contexts: list[tuple[str, str]]) -> str:
    context_block = "\n\n".join(content for _, content in contexts)
    return (
        "Answer the question using ONLY the context below. Be concise. "
        "If the context does not contain enough information to answer, "
        "set abstained to true. "
        "A number is complete if the sentence or clause around it continues normally "
        "(e.g. more words follow, or it ends with normal punctuation like a full stop "
        "after other text). Only treat a number as truncated when it sits at the very "
        "end of the provided context with nothing after it — e.g. a bare decimal point "
        "immediately followed by nothing. In that specific case, do not guess the "
        "missing digits — treat that fact as unavailable.\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {query}\nAnswer:"
    )


def generate(query: str, contexts: list[tuple[str, str]]) -> tuple[str, bool]:
    prompt = _build_prompt(query, contexts)
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
