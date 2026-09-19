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
    # Each excerpt is labeled and separated by a marker line, not just "\n\n"
    # — two unrelated excerpts can each be truncated at their own boundary
    # (a Node-chunking artifact, #31), and joining them bare lets a number
    # cut off at the end of one excerpt visually blend into the start of an
    # unrelated next excerpt (e.g. "...averaged 6." immediately followed by
    # "3 million barrels..." reads as "6.3", a fabricated cross-excerpt
    # number, not a guess from the model's own knowledge).
    context_block = "\n\n---\n\n".join(
        f"[Excerpt {i}]\n{content}" for i, (_, content) in enumerate(contexts, start=1)
    )
    return (
        "Answer the question using ONLY the context below. Be concise. "
        "If the context does not contain enough information to answer, "
        "set abstained to true. "
        "Each excerpt is a separate, possibly unrelated passage — never read the "
        "end of one excerpt as continuing into the next, even where they sit next "
        "to each other. A number is complete if the sentence or clause around it "
        "continues normally within its own excerpt. Only treat a number as "
        "truncated when it sits at the very end of its excerpt with nothing after "
        "it — e.g. a bare decimal point immediately followed by nothing. In that "
        "specific case, do not guess or borrow digits from a different excerpt — "
        "treat that fact as unavailable.\n\n"
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
