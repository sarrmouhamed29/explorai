"""How QdrantIndex.search maps Qdrant's error responses (no server needed)."""

import uuid
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
        dense=[1.0, 0.0],
        sparse=encode_query("attention"),
        flt=build_scope_filter(uuid.uuid4()),
        dense_limit=5,
        sparse_limit=5,
        limit=5,
    )


def response(status: int | None) -> UnexpectedResponse:
    return UnexpectedResponse(status, "Bad Request", SECRET_BODY, httpx.Headers())


def test_missing_alias_is_an_empty_result() -> None:
    assert search(response(404)) == []


# 400 is the realistic one: the API runs with a new embedding dimension while the
# alias still serves the old collection until re-indexing finishes.
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
