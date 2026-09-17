import eval.run_eval as run_eval_module
from eval.run_eval import run_eval

FIXTURE_GOLDEN_SET = "tests/fixtures/eval_golden_set.json"
FIXTURE_CORPUS_DIR = "tests/fixtures/eval_corpus"


def test_run_eval_produces_a_structurally_correct_report():
    report = run_eval(FIXTURE_GOLDEN_SET, FIXTURE_CORPUS_DIR)

    assert len(report["items"]) == 2
    assert report["overall"]["abstention_rate"] == 0.5
    assert report["overall"]["abstention_correctness_rate"] == 1.0

    answered = [item for item in report["items"] if not item["abstained"]]
    abstained = [item for item in report["items"] if item["abstained"]]
    assert len(answered) == 1
    assert len(abstained) == 1

    assert answered[0]["answer_relevancy"] is not None
    assert answered[0]["answer_relevancy"] > 0.5
    assert answered[0]["context_precision"] is not None

    # Abstained items are excluded from the triad metrics, not scored as 0
    assert abstained[0]["faithfulness"] is None
    assert abstained[0]["answer_relevancy"] is None
    assert abstained[0]["context_precision"] is None

    assert "core" in report["by_category"]
    assert "not_found_classification" in report["by_category"]


def test_run_eval_records_per_item_errors_without_crashing(monkeypatch):
    original_answer_query = run_eval_module.answer_query

    def flaky(query):
        if "favorite color" in query:
            raise RuntimeError("simulated failure")
        return original_answer_query(query)

    monkeypatch.setattr(run_eval_module, "answer_query", flaky)

    report = run_eval(FIXTURE_GOLDEN_SET, FIXTURE_CORPUS_DIR)

    errored = [item for item in report["items"] if item["error"] is not None]
    succeeded = [item for item in report["items"] if item["error"] is None]
    assert len(errored) == 1
    assert errored[0]["error"] == "simulated failure"
    assert len(succeeded) == 1
    # Aggregates are computed only from the successful item, not corrupted by the error
    assert report["overall"]["abstention_rate"] == 0.0
