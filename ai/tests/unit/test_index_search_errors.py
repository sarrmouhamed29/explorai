"""How QdrantIndex.search maps Qdrant's error responses (no server needed)."""

import uuid
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from explorai_ai.errors import RetrievalUnavailable
from explorai_ai.index import QdrantIndex
from explorai_ai.scope import build_scope_filter
from explorai_ai.sparse import encode_query

SECRET_BODY = (
    b'{"status":{"error":"Wrong input: Vector dimension error: expected 1024"}}'
)


class RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def query_points(self, **kwargs: Any) -> Any:
        raise self.error


def search(error: Exception) -> list[Any]:
    index = QdrantIndex(cast(QdrantClient, RaisingClient(error)))
    return index.search(
        collection="chunks_model_8",
        dense=[1.0, 0.0],
        sparse=encode_query("attention"),
        flt=build_scope_filter(uuid.uuid4()),
        dense_limit=5,
        sparse_limit=5,
        limit=5,
    )


def response(status: int | None) -> UnexpectedResponse:
    return UnexpectedResponse(status, "Bad Request", SECRET_BODY, httpx.Headers())


class RecordingClient:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def query_points(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(points=[])


def test_search_queries_the_named_collection_not_the_alias() -> None:
    client = RecordingClient()
    index = QdrantIndex(cast(QdrantClient, client), alias="chunks_current")
    index.search(
        collection="chunks_model_8",
        dense=[1.0, 0.0],
        sparse=encode_query("attention"),
        flt=build_scope_filter(uuid.uuid4()),
        dense_limit=5,
        sparse_limit=5,
        limit=5,
    )
    assert client.kwargs["collection_name"] == "chunks_model_8"


def test_missing_collection_is_an_empty_result() -> None:
    assert search(response(404)) == []


# Any other rejection (5xx, 401/403 for a wrong key, 429, a 400 for a malformed
# request) means the store cannot serve this search now.
@pytest.mark.parametrize("status", [400, 401, 403, 409, 422, 429, 500, 503, None])
def test_rejected_request_is_retrieval_unavailable(status: int | None) -> None:
    with pytest.raises(RetrievalUnavailable) as raised:
        search(response(status))
    assert str(raised.value) == "vector store not ready"
    # Nothing of the response (body, vector data) travels with the error.
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


@pytest.mark.parametrize(
    "error",
    [
        ResponseHandlingException(ValueError("bad json")),
        httpx.ConnectError("refused"),
    ],
)
def test_unreachable_store_is_retrieval_unavailable(error: Exception) -> None:
    with pytest.raises(RetrievalUnavailable, match="vector store unreachable"):
        search(error)


def test_other_errors_are_not_disguised() -> None:
    with pytest.raises(RuntimeError):
        search(RuntimeError("bug"))


def recorded_search(question: str) -> dict[str, Any]:
    client = RecordingClient()
    QdrantIndex(cast(QdrantClient, client)).search(
        collection="chunks_model_8",
        dense=[1.0, 0.0],
        sparse=encode_query(question),
        flt=build_scope_filter(uuid.uuid4()),
        dense_limit=5,
        sparse_limit=7,
        limit=3,
    )
    return client.kwargs


def test_a_question_without_tokens_searches_the_dense_vectors_only() -> None:
    # Punctuation only: no sparse term, so a sparse prefetch would be an empty
    # query vector. The dense search alone answers, under the same filter.
    assert encode_query("?! ...").indices == []
    kwargs = recorded_search("?! ...")
    assert "prefetch" not in kwargs
    assert kwargs["using"] == "dense"
    assert kwargs["query"] == [1.0, 0.0]
    assert kwargs["query_filter"] is not None
    assert kwargs["limit"] == 3


def test_a_question_with_tokens_fuses_dense_and_sparse() -> None:
    kwargs = recorded_search("attention heads")
    assert [p.using for p in kwargs["prefetch"]] == ["dense", "sparse"]
    assert kwargs["query"].fusion == "rrf"
