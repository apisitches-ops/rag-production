import csv
import html
import json
import os
import re
import unicodedata

import pymupdf

PAGE_RECT = pymupdf.paper_rect("a4")
MARGIN = 50


def _content_rect() -> pymupdf.Rect:
    return pymupdf.Rect(MARGIN, MARGIN, PAGE_RECT.width - MARGIN, PAGE_RECT.height - MARGIN)


def _write_pdf(text: str, path: str) -> None:
    with pymupdf.open() as doc:
        page = doc.new_page(width=PAGE_RECT.width, height=PAGE_RECT.height)
        escaped = html.escape(text).replace("\n", "<br>")
        page.insert_htmlbox(_content_rect(), escaped)
        doc.save(path)


def _normalize(text: str) -> str:
    # Line-wrapping can insert a space right after a hyphen (e.g. "AI-powered"
    # -> "AI- powered") without losing any content; collapse that specific
    # pattern instead of stripping all whitespace, which would also hide a
    # real bug that merges two separate words together (e.g. "New York" ->
    # "NewYork").
    text = re.sub(r"-\s+", "-", unicodedata.normalize("NFKD", text))
    return " ".join(text.split())


def _verify_roundtrip(path: str, source_text: str) -> None:
    with pymupdf.open(path) as pdf:
        extracted = "\n\n".join(page.get_text() for page in pdf)
    if _normalize(extracted) != _normalize(source_text):
        raise RuntimeError(f"round-trip check failed for {path}")


def _group_by_context(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(row["context"], []).append(row)
    return groups


def build(csv_path: str, corpus_dir: str, golden_set_path: str) -> None:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    groups = _group_by_context(rows)
    corpus_id_by_context = {context: f"{i:02d}" for i, context in enumerate(groups)}

    os.makedirs(corpus_dir, exist_ok=True)
    for context, corpus_id in corpus_id_by_context.items():
        pdf_path = os.path.join(corpus_dir, f"{corpus_id}.pdf")
        _write_pdf(context, pdf_path)
        _verify_roundtrip(pdf_path, context)

    golden_set = [
        {
            "query": row["query"],
            "expected_answer": row["answer"],
            "category": row["category"],
            "corpus_id": corpus_id_by_context[row["context"]],
        }
        for row in rows
    ]
    with open(golden_set_path, "w", encoding="utf-8") as f:
        json.dump(golden_set, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    build("train.csv", "eval/corpus", "eval/golden_set.json")
