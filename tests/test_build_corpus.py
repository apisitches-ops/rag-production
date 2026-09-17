import json

import pymupdf

from eval.build_corpus import _group_by_context, _normalize, _write_pdf, build


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


def test_write_pdf_round_trips_text_that_overflows_a_single_page(tmp_path):
    long_text = ("This is a sentence about PTT's sustainability targets. " * 100).strip()
    path = tmp_path / "long.pdf"

    _write_pdf(long_text, str(path))

    with pymupdf.open(str(path)) as pdf:
        extracted = "\n\n".join(page.get_text() for page in pdf)

    assert _normalize(extracted) == _normalize(long_text)


def test_normalize_does_not_hide_two_words_being_merged_together():
    # A real extraction bug that drops the space between "New" and "York"
    # must still be caught, even though normalize() tolerates the space
    # line-wrapping inserts after a hyphen.
    assert _normalize("New York office") != _normalize("NewYork office")


def test_build_produces_all_corpus_pdfs_and_golden_set_entries(tmp_path):
    corpus_dir = tmp_path / "corpus"
    golden_set_path = tmp_path / "golden_set.json"

    build("train.csv", str(corpus_dir), str(golden_set_path))

    golden_set = json.loads(golden_set_path.read_text())
    assert len(golden_set) == 200
    corpus_ids = {item["corpus_id"] for item in golden_set}
    assert len(corpus_ids) == 51
    for corpus_id in corpus_ids:
        assert (corpus_dir / f"{corpus_id}.pdf").exists()
    assert all(
        set(item.keys()) == {"query", "expected_answer", "category", "corpus_id"}
        for item in golden_set
    )
