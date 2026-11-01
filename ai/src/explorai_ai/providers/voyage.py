"""Voyage AI over REST: embeddings and reranking.

Verified against https://docs.voyageai.com/reference/embeddings-api and
https://docs.voyageai.com/reference/reranker-api (Bearer auth, `input_type` of
`document` or `query`, `output_dimension`, at most 1000 texts or documents per
request, results carry the input `index`).
"""

import logging
import math
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from explorai_ai.errors import ProviderError, ProviderRateLimited, ProviderUnavailable
from explorai_ai.models import RerankHit
from explorai_ai.retry import retry_transient

EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"
RERANK_URL = "https://api.voyageai.com/v1/rerank"
MAX_ITEMS_PER_REQUEST = 1000

logger = logging.getLogger(__name__)


def _post(
    client: httpx.Client, url: str, api_key: SecretStr, body: dict[str, Any]
) -> dict[str, Any]:
    """One POST, with vendor failures mapped to our errors (no key, no body echoed)."""
    try:
        response = client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
        )
    except httpx.TransportError as exc:  # timeouts and connection errors
        raise ProviderUnavailable(f"voyage unreachable: {type(exc).__name__}") from None
    status = response.status_code
    if status == 429:
        raise ProviderRateLimited("voyage rate limit (HTTP 429)")
    if status >= 500:
        raise ProviderUnavailable(f"voyage server error (HTTP {status})")
    if status >= 400:
        raise ProviderError(f"voyage rejected the request (HTTP {status})")
    try:
        payload = response.json()
    except ValueError:
        raise ProviderError("voyage returned a non-JSON response") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ProviderError("voyage returned an unexpected response shape")
    return payload


def _finite(value: Any) -> float:
    """A JSON number as a finite float; anything else is a provider error."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
    ):
        raise ProviderError("voyage returned a malformed vector value")
    return float(value)


def _ordered(items: list[Any], expected: int) -> list[dict[str, Any]]:
    """Return the items sorted by their `index`, checking that none is missing."""
    if len(items) != expected or not all(
        isinstance(item, dict) and isinstance(item.get("index"), int) for item in items
    ):
        raise ProviderError("voyage returned an unexpected number of results")
    ordered = sorted(items, key=lambda item: item["index"])
    if [item["index"] for item in ordered] != list(range(expected)):
        raise ProviderError("voyage returned inconsistent result indexes")
    return ordered


class VoyageEmbedder:
    def __init__(
        self,
        client: httpx.Client,
        *,
        api_key: SecretStr,
        model: str,
        dimension: int,
        batch_size: int,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model
        self.dimension = dimension
        self._batch_size = max(1, min(batch_size, MAX_ITEMS_PER_REQUEST))

    def __repr__(self) -> str:
        return f"VoyageEmbedder(model={self.model!r}, dimension={self.dimension})"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            vectors.extend(self._embed(batch, "document"))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]

    def _embed(self, batch: list[str], input_type: str) -> list[list[float]]:
        body = {
            "input": batch,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimension,
        }
        payload = retry_transient(
            lambda: _post(self._client, EMBEDDINGS_URL, self._api_key, body)
        )
        vectors: list[list[float]] = []
        for item in _ordered(payload["data"], len(batch)):
            vector = item.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimension:
                raise ProviderError("voyage returned a vector of the wrong dimension")
            vectors.append([_finite(value) for value in vector])
        return vectors


class VoyageReranker:
    def __init__(self, client: httpx.Client, *, api_key: SecretStr, model: str) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model

    def __repr__(self) -> str:
        return f"VoyageReranker(model={self.model!r})"

    def rerank(
        self, query: str, documents: Sequence[str], top_k: int
    ) -> list[RerankHit]:
        if not documents:
            return []
        if len(documents) > MAX_ITEMS_PER_REQUEST:
            raise ProviderError("too many documents to rerank in one request")
        body = {
            "query": query,
            "documents": list(documents),
            "model": self.model,
            "top_k": top_k,
        }
        payload = retry_transient(
            lambda: _post(self._client, RERANK_URL, self._api_key, body)
        )
        hits: list[RerankHit] = []
        seen: set[int] = set()
        for item in payload["data"]:
            index = item.get("index") if isinstance(item, dict) else None
            score = item.get("relevance_score") if isinstance(item, dict) else None
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(documents)
                or index in seen
                or isinstance(score, bool)
                or not isinstance(score, int | float)
                or not math.isfinite(score)
            ):
                raise ProviderError("voyage returned a malformed rerank result")
            seen.add(index)
            hits.append(RerankHit(index=index, score=float(score)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]
