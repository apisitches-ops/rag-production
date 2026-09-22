import json

from eval.build_corpus import _group_by_context, build


def test_group_by_context_groups_rows_sharing_the_same_context():
    rows = [
        {"context": "A", "query": "q1"},
        {"context": "B", "query": "q2"},
        {"context": "A", "query": "q3"},
    ]

    groups = _group_by_context(rows)

    assert list(groups.keys()) == ["A", "B"]
    assert len(groups["A"]) == 2
    assert len(groups["B"]) == 1


def test_build_produces_all_corpus_csvs_and_golden_set_entries(tmp_path):
    corpus_dir = tmp_path / "corpus"
    golden_set_path = tmp_path / "golden_set.json"

    build("eval/data/train.csv", str(corpus_dir), str(golden_set_path))

    golden_set = json.loads(golden_set_path.read_text())
    assert len(golden_set) == 200
    corpus_ids = {item["corpus_id"] for item in golden_set}
    assert len(corpus_ids) == 51
    for corpus_id in corpus_ids:
        assert (corpus_dir / f"{corpus_id}.csv").exists()
    assert all(
        set(item.keys()) == {"query", "expected_answer", "category", "corpus_id"}
        for item in golden_set
    )
