"""QdrantIndex against a real Qdrant (QDRANT_URL / QDRANT_API_KEY)."""

import os
import uuid
from collections.abc import Iterator

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import DeleteAlias, DeleteAliasOperation

from explorai_ai.errors import RetrievalUnavailable
from explorai_ai.index import IndexPoint, QdrantIndex
from explorai_ai.scope import build_scope_filter
from explorai_ai.sparse import encode_document, encode_query

pytestmark = pytest.mark.integration

DIM = 4


@pytest.fixture
def client() -> Iterator[QdrantClient]:
    url = os.environ.get("QDRANT_URL")
    if not url:
        pytest.fail("QDRANT_URL must be set for integration tests")
    qdrant = QdrantClient(url=url, api_key=os.environ.get("QDRANT_API_KEY"))
    yield qdrant
    qdrant.close()


@pytest.fixture
def index(client: QdrantClient) -> Iterator[QdrantIndex]:
    """An index on a unique alias; every collection it creates is dropped after."""
    alias = f"test_alias_{uuid.uuid4().hex[:8]}"
    idx = QdrantIndex(client, alias=alias)
    before = {c.name for c in client.get_collections().collections}
    yield idx
    if idx.alias_target() is not None:
        client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias))
            ]
        )
    for c in client.get_collections().collections:
        if c.name not in before and c.name.startswith("chunks_t"):
            client.delete_collection(c.name)


def fresh_model() -> str:
    return f"t{uuid.uuid4().hex[:8]}"


def point(
    dense: list[float],
    text: str,
    owner: str,
    pid: uuid.UUID | None = None,
) -> IndexPoint:
    return IndexPoint(
        id=pid or uuid.uuid4(),
        dense=dense,
        sparse=encode_document(text),
        owner=owner,
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
    )


def search(
    index: QdrantIndex,
    dense: list[float],
    text: str,
    user: uuid.UUID,
    scope: str = "all",
    branch_limit: int = 10,
) -> list[uuid.UUID]:
    hits = index.search(
        dense=dense,
        sparse=encode_query(text),
        flt=build_scope_filter(user, scope),  # type: ignore[arg-type]
        dense_limit=branch_limit,
        sparse_limit=branch_limit,
        limit=10,
    )
    return [h.id for h in hits]


def test_ensure_collection_is_idempotent(index: QdrantIndex) -> None:
    model = fresh_model()
    first = index.ensure_collection(model, DIM)
    second = index.ensure_collection(model, DIM)
    assert first == second == f"chunks_{model}_{DIM}"


def test_collection_exists_follows_creation_and_deletion(
    index: QdrantIndex, client: QdrantClient
) -> None:
    model = fresh_model()
    name = index.collection_name(model, DIM)
    assert index.collection_exists(name) is False
    index.ensure_collection(model, DIM)
    assert index.collection_exists(name) is True
    client.delete_collection(name)
    assert index.collection_exists(name) is False


def test_upsert_and_hybrid_search_finds_semantic_and_keyword_matches(
    index: QdrantIndex,
) -> None:
    """Each branch is the only way to reach one of the points (branch limit 2).

    Three distractors have dense vectors closer to the query than the keyword point
    and share no token with the query, so the keyword point cannot come from the
    dense branch (it ranks 5th of 5 there); the semantic point shares no token with
    the query, so it cannot come from the sparse branch. Dropping either prefetch
    makes this test fail.
    """
    user = uuid.uuid4()
    collection = index.ensure_collection(fresh_model(), DIM)
    index.switch_alias(collection)
    semantic = point([1.0, 0.0, 0.0, 0.0], "alpha beta gamma", "public")
    keyword = point([0.0, 0.0, 0.0, 1.0], "zyxwvutsr rare token", "public")
    distractors = [
        point([1.0, 0.1 * (i + 1), 0.0, 0.0], f"filler{i} words", "public")
        for i in range(3)
    ]
    index.upsert(collection, [semantic, keyword, *distractors])
    found = search(index, [1.0, 0.0, 0.0, 0.0], "zyxwvutsr", user, branch_limit=2)
    assert semantic.id in found  # only the dense branch can reach it
    assert keyword.id in found  # only the sparse branch can reach it


def test_search_before_any_alias_returns_empty(index: QdrantIndex) -> None:
    assert index.alias_target() is None
    assert search(index, [1.0, 0.0, 0.0, 0.0], "anything", uuid.uuid4()) == []


def test_search_with_alias_pointing_at_a_deleted_collection_returns_empty(
    index: QdrantIndex, client: QdrantClient
) -> None:
    collection = index.ensure_collection(fresh_model(), DIM)
    index.switch_alias(collection)
    client.delete_collection(collection)
    assert search(index, [1.0, 0.0, 0.0, 0.0], "anything", uuid.uuid4()) == []


def test_ensure_collection_restores_missing_payload_indexes(
    index: QdrantIndex, client: QdrantClient
) -> None:
    model = fresh_model()
    name = index.ensure_collection(model, DIM)
    client.delete_payload_index(name, "owner_id")
    client.delete_payload_index(name, "document_id")
    assert client.get_collection(name).payload_schema == {}
    assert index.ensure_collection(model, DIM) == name
    schema = client.get_collection(name).payload_schema
    assert set(schema) == {"owner_id", "document_id"}


def test_filter_limits_results_to_owner_and_public(index: QdrantIndex) -> None:
    me, other = uuid.uuid4(), uuid.uuid4()
    collection = index.ensure_collection(fresh_model(), DIM)
    index.switch_alias(collection)
    mine = point([1.0, 0.0, 0.0, 0.0], "same words", str(me))
    public = point([1.0, 0.0, 0.0, 0.0], "same words", "public")
    theirs = point([1.0, 0.0, 0.0, 0.0], "same words", str(other))
    index.upsert(collection, [mine, public, theirs])
    found = search(index, [1.0, 0.0, 0.0, 0.0], "same words", me)
    assert set(found) == {mine.id, public.id}


def test_filter_mine_excludes_public(index: QdrantIndex) -> None:
    me = uuid.uuid4()
    collection = index.ensure_collection(fresh_model(), DIM)
    index.switch_alias(collection)
    mine = point([1.0, 0.0, 0.0, 0.0], "same words", str(me))
    public = point([1.0, 0.0, 0.0, 0.0], "same words", "public")
    index.upsert(collection, [mine, public])
    found = search(index, [1.0, 0.0, 0.0, 0.0], "same words", me, scope="mine")
    assert found == [mine.id]


def test_delete_removes_points(index: QdrantIndex) -> None:
    user = uuid.uuid4()
    collection = index.ensure_collection(fresh_model(), DIM)
    index.switch_alias(collection)
    keep = point([1.0, 0.0, 0.0, 0.0], "keep words", "public")
    drop = point([1.0, 0.0, 0.0, 0.0], "drop words", "public")
    index.upsert(collection, [keep, drop])
    index.delete(collection, [drop.id])
    assert search(index, [1.0, 0.0, 0.0, 0.0], "words", user) == [keep.id]


def test_alias_switch_is_atomic_and_search_follows_it(index: QdrantIndex) -> None:
    user = uuid.uuid4()
    first = index.ensure_collection(fresh_model(), DIM)
    second = index.ensure_collection(fresh_model(), DIM)
    old = point([1.0, 0.0, 0.0, 0.0], "old words", "public")
    new = point([1.0, 0.0, 0.0, 0.0], "new words", "public")
    index.upsert(first, [old])
    index.upsert(second, [new])
    assert index.alias_target() is None
    index.switch_alias(first)
    assert index.alias_target() == first
    assert search(index, [1.0, 0.0, 0.0, 0.0], "words", user) == [old.id]
    index.switch_alias(second)
    assert index.alias_target() == second
    assert search(index, [1.0, 0.0, 0.0, 0.0], "words", user) == [new.id]


def test_point_ids_lists_all_points(index: QdrantIndex) -> None:
    collection = index.ensure_collection(fresh_model(), DIM)
    points = [point([1.0, 0.0, 0.0, 0.0], f"word{i}", "public") for i in range(300)]
    index.upsert(collection, points)
    assert set(index.point_ids(collection)) == {p.id for p in points}


def test_unreachable_qdrant_raises_retrieval_unavailable() -> None:
    dead = QdrantClient(url="http://127.0.0.1:1", timeout=2)
    index = QdrantIndex(dead)
    with pytest.raises(RetrievalUnavailable):
        index.search(
            dense=[1.0, 0.0, 0.0, 0.0],
            sparse=encode_query("anything"),
            flt=build_scope_filter(uuid.uuid4()),
            dense_limit=5,
            sparse_limit=5,
            limit=5,
        )
