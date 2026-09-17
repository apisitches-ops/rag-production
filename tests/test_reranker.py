from app.reranker import rerank


def test_rerank_puts_the_relevant_candidate_first():
    query = "What year does PTT aim to achieve Net Zero emissions?"
    candidates = [
        ("filler-1", "Chocolate cake recipes require flour, sugar, and cocoa powder."),
        ("filler-2", "Gardening tips: water your tomato plants early in the morning."),
        ("relevant", "PTT committed to achieving Net Zero greenhouse gas emissions by the year 2050."),
        ("filler-3", "The history of steam engines dates back to the 18th century."),
    ]

    result = rerank(query, candidates)

    assert result[0][0] == "relevant"
    assert {node_id for node_id, _ in result} == {node_id for node_id, _ in candidates}


def test_rerank_returns_empty_for_no_candidates():
    assert rerank("anything", []) == []


def test_rerank_ranks_correctly_across_batch_boundaries():
    query = "What year does PTT aim to achieve Net Zero emissions?"
    filler = [
        (f"filler-{i}", f"Unrelated filler passage number {i} about gardening and recipes.")
        for i in range(20)
    ]
    candidates = filler + [
        ("relevant", "PTT committed to achieving Net Zero greenhouse gas emissions by the year 2050.")
    ]

    result = rerank(query, candidates)

    assert result[0][0] == "relevant"
    assert {node_id for node_id, _ in result} == {node_id for node_id, _ in candidates}
