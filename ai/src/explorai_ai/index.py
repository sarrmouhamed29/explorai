"""Qdrant adapter: hybrid (dense + sparse, RRF) search behind an alias.

The only module that talks to Qdrant. Filters are built by ``scope.build_scope_filter``
and passed in; this module never builds one. Points live in a collection named after
the embedding model and dimension; the alias ``chunks_current`` points at the
collection searches use, so a re-index into a new collection goes live with one
atomic alias switch and no downtime.
"""

import logging
import re
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from uuid import UUID

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    Filter,
    Fusion,
    FusionQuery,
    KeywordIndexParams,
    KeywordIndexType,
    Modifier,
    PointIdsList,
    PointStruct,
    Prefetch,
    SparseVectorParams,
    VectorParams,
)
from qdrant_client.models import SparseVector as QdrantSparseVector

from explorai_ai.errors import RetrievalUnavailable
from explorai_ai.models import ScoredId
from explorai_ai.sparse import SparseVector

DENSE = "dense"
SPARSE = "sparse"
DEFAULT_ALIAS = "chunks_current"
_SCROLL_PAGE = 256
logger = logging.getLogger(__name__)


def is_missing(exc: UnexpectedResponse) -> bool:
    """The server answered 404: the alias or the collection it names does not exist."""
    return exc.status_code == 404


@dataclass(frozen=True)
class IndexPoint:
    id: UUID
    dense: list[float]
    sparse: SparseVector
    owner: str
    document_id: UUID
    version_id: UUID


class QdrantIndex:
    def __init__(self, client: QdrantClient, *, alias: str = DEFAULT_ALIAS) -> None:
        self._client = client
        self._alias = alias

    @staticmethod
    def collection_name(model: str, dimension: int) -> str:
        """The collection, and index name in ``chunk_index_entries``, of a model.

        Model *and* dimension: the same model at another dimension is another index.
        """
        raw = f"chunks_{model}_{dimension}".lower()
        return re.sub(r"[^a-z0-9_]", "_", raw)

    def collection_exists(self, name: str) -> bool:
        return self._client.collection_exists(name)

    def ensure_collection(self, model: str, dimension: int) -> str:
        name = self.collection_name(model, dimension)
        if not self._client.collection_exists(name):
            self._client.create_collection(
                collection_name=name,
                vectors_config={
                    DENSE: VectorParams(size=dimension, distance=Distance.COSINE)
                },
                # IDF is applied by Qdrant at query time from the collection stats.
                sparse_vectors_config={
                    SPARSE: SparseVectorParams(modifier=Modifier.IDF)
                },
            )
        # Runs even when the collection exists: a crash between the creation above and
        # the indexes below would otherwise leave it without its tenant index for good.
        # Only the missing indexes are created, so no reliance on re-creation semantics.
        present = self._client.get_collection(name).payload_schema
        if "owner_id" not in present:
            # Tenant index: Qdrant co-locates the vectors of one owner.
            self._client.create_payload_index(
                collection_name=name,
                field_name="owner_id",
                field_schema=KeywordIndexParams(
                    type=KeywordIndexType.KEYWORD, is_tenant=True
                ),
            )
        if "document_id" not in present:
            self._client.create_payload_index(
                collection_name=name,
                field_name="document_id",
                field_schema=KeywordIndexParams(type=KeywordIndexType.KEYWORD),
            )
        return name

    def upsert(self, collection: str, points: Sequence[IndexPoint]) -> None:
        if not points:
            return
        self._client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=str(p.id),
                    vector={
                        DENSE: p.dense,
                        SPARSE: QdrantSparseVector(
                            indices=p.sparse.indices, values=p.sparse.values
                        ),
                    },
                    payload={
                        "owner_id": p.owner,
                        "document_id": str(p.document_id),
                        "version_id": str(p.version_id),
                    },
                )
                for p in points
            ],
        )

    def delete(self, collection: str, ids: Sequence[UUID]) -> None:
        if not ids:
            return
        self._client.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=[str(i) for i in ids]),
        )

    def search(
        self,
        *,
        dense: list[float],
        sparse: SparseVector,
        flt: Filter,
        dense_limit: int,
        sparse_limit: int,
        limit: int,
    ) -> list[ScoredId]:
        """Dense and sparse candidates fused by Reciprocal Rank Fusion.

        The filter goes on each prefetch (candidates are selected there) and on the
        fusion stage too, so no candidate can bypass it.
        """
        try:
            response = self._client.query_points(
                collection_name=self._alias,
                prefetch=[
                    Prefetch(query=dense, using=DENSE, filter=flt, limit=dense_limit),
                    Prefetch(
                        query=QdrantSparseVector(
                            indices=sparse.indices, values=sparse.values
                        ),
                        using=SPARSE,
                        filter=flt,
                        limit=sparse_limit,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                query_filter=flt,
                limit=limit,
                with_payload=False,
                with_vectors=False,
            )
        except (ResponseHandlingException, httpx.HTTPError) as exc:
            raise RetrievalUnavailable("vector store unreachable") from exc
        except UnexpectedResponse as exc:
            if is_missing(exc):
                # No alias yet (nothing indexed) or it points at a deleted collection:
                # an empty corpus, not an outage. No query text is logged.
                logger.warning("vector store has no searchable collection")
                return []
            # Any other answer (5xx, but also 400 "vector dimension error" while
            # the alias still serves the collection of a previous embedding
            # dimension, 401/403 for a wrong key, 429...) means the store cannot
            # serve this search now: an outage for the caller (503), never a raw
            # 500. Fixed message and no chained cause: the response body may echo
            # request data, and nothing of it must reach logs or clients.
            logger.warning(
                "vector store rejected the search (HTTP %s)", exc.status_code
            )
            raise RetrievalUnavailable("vector store not ready") from None
        return [
            ScoredId(id=uuid.UUID(str(p.id)), score=p.score) for p in response.points
        ]

    def alias_target(self) -> str | None:
        for alias in self._client.get_aliases().aliases:
            if alias.alias_name == self._alias:
                return alias.collection_name
        return None

    def switch_alias(self, collection: str) -> None:
        """Repoint the alias in one request: Qdrant applies its operations atomically.

        Deleting an alias that does not exist is an error, so the delete is only sent
        when the alias exists (the first switch just creates it).
        """
        operations: list[DeleteAliasOperation | CreateAliasOperation] = []
        if self.alias_target() is not None:
            operations.append(
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=self._alias))
            )
        operations.append(
            CreateAliasOperation(
                create_alias=CreateAlias(
                    collection_name=collection, alias_name=self._alias
                )
            )
        )
        self._client.update_collection_aliases(change_aliases_operations=operations)

    def point_ids(self, collection: str) -> Iterator[UUID]:
        offset: object = None
        while True:
            records, offset = self._client.scroll(
                collection_name=collection,
                limit=_SCROLL_PAGE,
                offset=offset,  # type: ignore[arg-type]
                with_payload=False,
                with_vectors=False,
            )
            for record in records:
                yield uuid.UUID(str(record.id))
            if offset is None:
                return
