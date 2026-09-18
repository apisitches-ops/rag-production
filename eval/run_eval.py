import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import pandas as pd
from datasets import Dataset
from langchain_community.chat_models import ChatOllama
from langchain_community.embeddings import OllamaEmbeddings
from ragas import evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, faithfulness
from ragas.run_config import RunConfig

from app import db
from app.ingest import ingest_document
from app.ollama import EMBED_MODEL
from app.query import DEV_GENERATOR_MODEL, answer_query

TRIAD_METRICS = [faithfulness, answer_relevancy, context_precision]
TRIAD_METRIC_NAMES = [metric.name for metric in TRIAD_METRICS]


def _ingest_corpus(corpus_dir: str) -> None:
    for path in sorted(Path(corpus_dir).glob("*.pdf")):
        ingest_document(str(path), document_name=path.stem)


def _clean(value: object) -> float | None:
    return None if pd.isna(value) else float(value)  # type: ignore[arg-type]


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def _should_abstain(item: dict) -> bool:
    return item["category"] == "not_found_classification"


def _score_answered_items(answered: list[dict]) -> None:
    llm = LangchainLLMWrapper(ChatOllama(model=DEV_GENERATOR_MODEL, temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OllamaEmbeddings(model=EMBED_MODEL))

    dataset = Dataset.from_dict(
        {
            "question": [item["query"] for item in answered],
            "answer": [item["answer"] for item in answered],
            "contexts": [item["contexts"] for item in answered],
            "ground_truth": [item["expected_answer"] for item in answered],
        }
    )
    # Ragas defaults to 16 concurrent calls, which overwhelms a single local
    # Ollama instance and causes widespread TimeoutErrors instead of scores.
    run_config = RunConfig(max_workers=2)
    result_df = evaluate(
        dataset, metrics=TRIAD_METRICS, llm=llm, embeddings=embeddings, run_config=run_config
    ).to_pandas()

    for i, item in enumerate(answered):
        for name in TRIAD_METRIC_NAMES:
            item[name] = _clean(result_df.loc[i, name])


def _aggregate(items: list[dict]) -> dict:
    if not items:
        return {name: None for name in [*TRIAD_METRIC_NAMES, "abstention_rate", "abstention_correctness_rate"]}
    aggregate = {name: _mean([item[name] for item in items]) for name in TRIAD_METRIC_NAMES}
    aggregate["abstention_rate"] = mean(1.0 if item["abstained"] else 0.0 for item in items)
    aggregate["abstention_correctness_rate"] = mean(
        1.0 if item["abstained"] == _should_abstain(item) else 0.0 for item in items
    )
    return aggregate


def run_eval(golden_set_path: str, corpus_dir: str, reports_dir: str | None = None) -> dict:
    with open(golden_set_path, encoding="utf-8") as f:
        golden_set = json.load(f)

    db.reset()
    _ingest_corpus(corpus_dir)

    items = []
    for i, entry in enumerate(golden_set):
        try:
            result = answer_query(entry["query"])
        except Exception as exc:
            items.append({**entry, "error": str(exc)})
            print(f"[{i + 1}/{len(golden_set)}] {entry['category']}: error ({exc})", flush=True)
            continue
        items.append(
            {
                **entry,
                "error": None,
                "answer": result["answer"],
                "citations": result["citations"],
                "contexts": result["contexts"],
                "abstained": result["abstained"],
                "faithfulness": None,
                "answer_relevancy": None,
                "context_precision": None,
            }
        )
        status = "abstained" if result["abstained"] else "answered"
        print(f"[{i + 1}/{len(golden_set)}] {entry['category']}: {status}", flush=True)

    successful = [item for item in items if item["error"] is None]
    answered = [item for item in successful if not item["abstained"]]
    if answered:
        print(f"scoring {len(answered)} answered items with Ragas...", flush=True)
        _score_answered_items(answered)
        print("scoring done", flush=True)

    categories = sorted({item["category"] for item in items})
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall": _aggregate(successful),
        "by_category": {
            category: _aggregate([item for item in successful if item["category"] == category])
            for category in categories
        },
        "items": items,
    }

    if reports_dir is not None:
        os.makedirs(reports_dir, exist_ok=True)
        report_path = os.path.join(reports_dir, f"{report['timestamp']}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    return report


if __name__ == "__main__":
    run_eval("eval/golden_set.json", "eval/corpus", reports_dir="eval/reports")
