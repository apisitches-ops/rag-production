import csv
import json
import os


def _group_by_context(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(row["context"], []).append(row)
    return groups


def _write_csv(text: str, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["content"])
        writer.writerow([text])


def build(csv_path: str, corpus_dir: str, golden_set_path: str) -> None:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    groups = _group_by_context(rows)
    corpus_id_by_context = {context: f"{i:02d}" for i, context in enumerate(groups)}

    os.makedirs(corpus_dir, exist_ok=True)
    for context, corpus_id in corpus_id_by_context.items():
        _write_csv(context, os.path.join(corpus_dir, f"{corpus_id}.csv"))

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
