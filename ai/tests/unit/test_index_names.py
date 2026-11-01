import httpx
from qdrant_client.http.exceptions import UnexpectedResponse

from explorai_ai.index import QdrantIndex, is_missing, is_unavailable


def test_collection_name_is_sanitised() -> None:
    assert QdrantIndex.collection_name("Voyage-3.5/Lite", 1024) == (
        "chunks_voyage_3_5_lite_1024"
    )


def fake(status: int | None) -> UnexpectedResponse:
    return UnexpectedResponse(status, "x", b"", httpx.Headers())


def test_error_classification() -> None:
    assert is_missing(fake(404))
    assert not is_missing(fake(500))
    assert is_unavailable(fake(503))
    assert not is_unavailable(fake(404))
    assert not is_unavailable(fake(400))
    assert not is_unavailable(fake(None))
