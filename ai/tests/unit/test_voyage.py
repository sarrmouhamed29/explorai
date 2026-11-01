import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from explorai_ai.errors import ProviderError, ProviderRateLimited, ProviderUnavailable
from explorai_ai.providers import voyage
from explorai_ai.providers.voyage import VoyageEmbedder, VoyageReranker

KEY = "sk-test-secret-1234"
DIM = 4


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("explorai_ai.retry.time.sleep", lambda _seconds: None)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def embedder(client: httpx.Client, batch_size: int = 64) -> VoyageEmbedder:
    return VoyageEmbedder(
        client,
        api_key=SecretStr(KEY),
        model="voyage-4",
        dimension=DIM,
        batch_size=batch_size,
    )


def embedding_response(
    request: httpx.Request, *, reverse: bool = False, dim: int = DIM
) -> httpx.Response:
    texts = json.loads(request.content)["input"]
    data = [
        {"object": "embedding", "index": i, "embedding": [float(len(t))] * dim}
        for i, t in enumerate(texts)
    ]
    if reverse:
        data.reverse()
    return httpx.Response(200, json={"object": "list", "data": data})


def test_embed_documents_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return embedding_response(request)

    result = embedder(make_client(handler)).embed_documents(["a", "bb"])
    assert result == [[1.0] * DIM, [2.0] * DIM]
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.voyageai.com/v1/embeddings"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "input": ["a", "bb"],
        "model": "voyage-4",
        "input_type": "document",
        "output_dimension": DIM,
    }


def test_embed_query_uses_query_input_type() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return embedding_response(request)

    assert embedder(make_client(handler)).embed_query("hello") == [5.0] * DIM
    assert seen[0]["input_type"] == "query"
    assert seen[0]["input"] == ["hello"]


def test_150_texts_are_sent_in_three_batches_in_order() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["input"]))
        return embedding_response(request)

    texts = ["x" * (i + 1) for i in range(150)]
    result = embedder(make_client(handler), batch_size=64).embed_documents(texts)
    assert sizes == [64, 64, 22]
    assert [vector[0] for vector in result] == [float(i + 1) for i in range(150)]


def test_batch_size_is_capped_by_the_provider_maximum() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["input"]))
        return embedding_response(request)

    embedder(make_client(handler), batch_size=5000).embed_documents(["a"] * 1001)
    assert sizes == [voyage.MAX_ITEMS_PER_REQUEST, 1]


def test_empty_input_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no call expected")

    assert embedder(make_client(handler)).embed_documents([]) == []


def test_response_order_is_restored_by_index() -> None:
    client = make_client(lambda r: embedding_response(r, reverse=True))
    result = embedder(client).embed_documents(["a", "bb", "ccc"])
    assert [vector[0] for vector in result] == [1.0, 2.0, 3.0]


def test_wrong_dimension_raises_provider_error() -> None:
    client = make_client(lambda r: embedding_response(r, dim=DIM + 1))
    with pytest.raises(ProviderError, match="dimension"):
        embedder(client).embed_documents(["a"])


def test_missing_result_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with pytest.raises(ProviderError):
        embedder(make_client(handler)).embed_documents(["a"])


def test_rate_limit_then_success_is_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"detail": "slow down"})
        return embedding_response(request)

    assert embedder(make_client(handler)).embed_documents(["a"]) == [[1.0] * DIM]
    assert calls == 2


def test_server_error_exhausted_raises_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"detail": "boom"})

    with pytest.raises(ProviderUnavailable) as caught:
        embedder(make_client(handler)).embed_documents(["a"])
    assert calls == 3
    assert not isinstance(caught.value, ProviderRateLimited)


def test_persistent_rate_limit_raises_rate_limited() -> None:
    client = make_client(lambda r: httpx.Response(429))
    with pytest.raises(ProviderRateLimited):
        embedder(client).embed_query("a")


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_client_errors_are_not_retried_and_hide_the_key(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"detail": f"bad key {KEY}"})

    with pytest.raises(ProviderError) as caught:
        embedder(make_client(handler)).embed_documents(["a"])
    assert calls == 1
    assert not isinstance(caught.value, ProviderUnavailable)
    assert KEY not in str(caught.value)
    assert KEY not in repr(caught.value)


def test_timeout_and_connection_errors_are_unavailable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler in (timeout, refused):
        with pytest.raises(ProviderUnavailable) as caught:
            embedder(make_client(handler)).embed_query("a")
        assert KEY not in str(caught.value)


def test_non_json_response_raises_provider_error() -> None:
    client = make_client(lambda r: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(ProviderError):
        embedder(client).embed_query("a")


def test_repr_never_shows_the_key() -> None:
    client = make_client(lambda r: httpx.Response(200))
    assert KEY not in repr(embedder(client))
    reranker = VoyageReranker(client, api_key=SecretStr(KEY), model="rerank-2.5")
    assert KEY not in repr(reranker)


def rerank_handler(
    scores: dict[int, float], seen: list[httpx.Request] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        data = [
            {"index": index, "relevance_score": score}
            for index, score in sorted(scores.items())
        ]
        return httpx.Response(200, json={"object": "list", "data": data})

    return handler


def test_rerank_request_shape_and_sorted_results() -> None:
    seen: list[httpx.Request] = []
    client = make_client(rerank_handler({0: 0.1, 1: 0.9, 2: 0.5}, seen))
    reranker = VoyageReranker(client, api_key=SecretStr(KEY), model="rerank-2.5")
    hits = reranker.rerank("q", ["a", "b", "c"], top_k=2)
    assert [(h.index, h.score) for h in hits] == [(1, 0.9), (2, 0.5)]
    assert str(seen[0].url) == "https://api.voyageai.com/v1/rerank"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(seen[0].content) == {
        "query": "q",
        "documents": ["a", "b", "c"],
        "model": "rerank-2.5",
        "top_k": 2,
    }


def test_rerank_honours_top_k_even_if_provider_returns_more() -> None:
    client = make_client(rerank_handler({0: 0.1, 1: 0.9, 2: 0.5}))
    reranker = VoyageReranker(client, api_key=SecretStr(KEY), model="m")
    assert [h.index for h in reranker.rerank("q", ["a", "b", "c"], top_k=1)] == [1]


def test_rerank_rejects_out_of_range_index() -> None:
    client = make_client(rerank_handler({7: 0.9}))
    reranker = VoyageReranker(client, api_key=SecretStr(KEY), model="m")
    with pytest.raises(ProviderError):
        reranker.rerank("q", ["a"], top_k=1)


def test_rerank_no_documents_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no call expected")

    reranker = VoyageReranker(make_client(handler), api_key=SecretStr(KEY), model="m")
    assert reranker.rerank("q", [], top_k=3) == []


def test_rerank_unauthorised_raises_provider_error() -> None:
    client = make_client(lambda r: httpx.Response(401))
    reranker = VoyageReranker(client, api_key=SecretStr(KEY), model="m")
    with pytest.raises(ProviderError) as caught:
        reranker.rerank("q", ["a"], top_k=1)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize(
    "bad_value",
    [None, "x", float("nan"), float("inf"), float("-inf"), [1.0], True],
)
def test_malformed_vector_element_raises_provider_error(bad_value: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        vector = [0.5] * (DIM - 1) + [bad_value]
        body = json.dumps({"data": [{"index": 0, "embedding": vector}]}, allow_nan=True)
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "application/json"}
        )

    with pytest.raises(ProviderError, match="malformed vector"):
        embedder(make_client(handler)).embed_query("a")


def test_rerank_rejects_duplicate_indexes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = [
            {"index": 0, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.8},
            {"index": 1, "relevance_score": 0.1},
        ]
        return httpx.Response(200, json={"data": data})

    reranker = VoyageReranker(make_client(handler), api_key=SecretStr(KEY), model="m")
    with pytest.raises(ProviderError, match="malformed rerank"):
        reranker.rerank("q", ["a", "b"], top_k=2)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), None, "0.5", True])
def test_rerank_rejects_non_finite_or_non_numeric_scores(score: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"data": [{"index": 0, "relevance_score": score}]})
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "application/json"}
        )

    reranker = VoyageReranker(make_client(handler), api_key=SecretStr(KEY), model="m")
    with pytest.raises(ProviderError, match="malformed rerank"):
        reranker.rerank("q", ["a"], top_k=1)
