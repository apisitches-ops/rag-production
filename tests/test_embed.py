from app.embeddings import embed


def test_embed_returns_vectors_matching_the_pgvector_schema_dimension():
    vectors = embed(["What year does PTT aim to achieve Net Zero emissions?"])

    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
