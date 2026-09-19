import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, List, Optional

import pandas as pd
from datasets import Dataset
# VoyageEmbeddings here (not the newer langchain-voyageai package) because no
# version of langchain-voyageai supports this project's pinned
# langchain-core==0.2.43 (0.1.x needs <0.2, 0.1.4+ needs >=0.3.29) — accepted
# deprecation warning rather than upgrading langchain-core and risking the
# Ragas import breakage ADR/progress-log already pinned around.
from langchain_community.embeddings import VoyageEmbeddings
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from ragas import evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, faithfulness
from ragas.run_config import RunConfig

from app import db
from app.embeddings import EMBED_MODEL
from app.generator import GENERATOR_MODEL
from app.ingest import ingest_document
from app.query import answer_query

TRIAD_METRICS = [faithfulness, answer_relevancy, context_precision]
TRIAD_METRIC_NAMES = [metric.name for metric in TRIAD_METRICS]


class _JudgeLLM(ChatGoogleGenerativeAI):
    """Ragas's LangchainLLMWrapper always passes a per-call `temperature`
    override (ragas/llms/base.py's generate_text/agenerate_text). This
    langchain-google-genai version (pinned to 1.0.10 — see the pyproject.toml
    comment) forwards that kwarg straight into the low-level
    GenerativeServiceClient.generate_content(), which doesn't accept it,
    raising TypeError on every call. Dropping it here and relying on the
    temperature set at construction (0, for deterministic judging) is
    equivalent for this project's only use of temperature."""

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        kwargs.pop("temperature", None)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        kwargs.pop("temperature", None)
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _ingest_corpus(corpus_dir: str) -> None:
    for path in sorted(Path(corpus_dir).glob("*.csv")):
        ingest_document(str(path), document_name=path.stem)


def _clean(value: object) -> float | None:
    return None if pd.isna(value) else float(value)  # type: ignore[arg-type]


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def _should_abstain(item: dict) -> bool:
    return item["category"] == "not_found_classification"


def _score_answered_items(answered: list[dict]) -> None:
    # Judge and pipeline intentionally share the same models now (ticket #27
    # — see #22's "provider consistency" Implementation Decision). Before
    # #26, the judge had to be decoupled onto separate JUDGE_LLM_MODEL/
    # JUDGE_EMBED_MODEL constants because the pipeline was still on Ollama —
    # that decoupling no longer serves any purpose now both sides are
    # Gemini/Voyage AI, so it was removed rather than kept as dead
    # indirection.
    #
    # Both constructors work fine at runtime with only these kwargs (their
    # other fields have pydantic defaults) — mypy's synthesized signatures
    # for these two packages don't reflect that, hence the ignores below.
    llm = LangchainLLMWrapper(
        _JudgeLLM(  # type: ignore[call-arg]
            model=GENERATOR_MODEL, temperature=0, google_api_key=os.environ["GEMINI_API_KEY"]
        )
    )
    # max_retries: the judge's embedding calls compete for the same rate-
    # limited Voyage AI account (3 RPM, no payment method on file — see
    # app/embeddings.py) as the pipeline's own ingest/query embedding calls
    # that just ran. The default of 6 retries capped at a 10s wait wasn't
    # enough to reliably survive that — same deviation already accepted for
    # app/embeddings.py in ticket #24.
    embeddings = LangchainEmbeddingsWrapper(
        VoyageEmbeddings(model=EMBED_MODEL, max_retries=10)  # type: ignore[call-arg]
    )

    dataset = Dataset.from_dict(
        {
            "question": [item["query"] for item in answered],
            "answer": [item["answer"] for item in answered],
            "contexts": [item["contexts"] for item in answered],
            "ground_truth": [item["expected_answer"] for item in answered],
        }
    )
    # Ragas defaults to 16 concurrent calls. Originally lowered because that
    # overwhelmed a single local Ollama instance; kept low now that the judge
    # is Gemini + Voyage AI (ticket #27) since the Voyage AI dev account is
    # separately capped at 3 RPM (see app/embeddings.py) — high concurrency
    # here would just produce widespread RateLimitErrors instead of scores.
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
