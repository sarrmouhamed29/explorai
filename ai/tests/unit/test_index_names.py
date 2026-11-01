from explorai_ai.index import QdrantIndex


def test_collection_name_is_sanitised() -> None:
    assert QdrantIndex.collection_name("Voyage-3.5/Lite", 1024) == (
        "chunks_voyage_3_5_lite_1024"
    )
