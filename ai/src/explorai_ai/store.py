"""Chunk texts re-read from PostgreSQL under Row-Level Security.

Qdrant only returns chunk ids; the text and its document always come from here,
through ``ScopedSessions.scoped(user_id)``. So an id that leaked from the vector
index (an orphan point, a filter bug) can never turn into another user's text:
the RLS policies of migration 0005 hide the row, and the explicit owner condition
below states the same rule in the query (the policy is the safety net).

The second guard is staleness: the index may still hold points of an older
version, or of a version that has since been found to be a duplicate, until
``prune-index`` removes them. :func:`is_eligible_version` drops those.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, or_, select, tuple_
from sqlalchemy.orm import aliased
from sqlalchemy.orm.util import AliasedClass

from explorai_db.models import Chunk, ChunkIndexEntry, Document, DocumentVersion
from explorai_db.rls import ScopedSessions

type VersionEntity = type[DocumentVersion] | AliasedClass[DocumentVersion]


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    document_title: str | None
    heading: str | None
    text: str


def is_latest_ok_version(version: VersionEntity) -> ColumnElement[bool]:
    """``version`` is the latest ``ok`` version of its document.

    Latest means no other ``ok`` version of the same document is newer by
    ``(created_at, id)``: the id breaks ties between equal timestamps, larger
    wins, so exactly one version qualifies per document that has an ``ok`` one.
    Whether it is a duplicate does not matter here (see
    :func:`is_eligible_version`).
    """
    newer = aliased(DocumentVersion, name="newer_version")
    return and_(
        version.status == "ok",
        ~select(newer.id)
        .where(
            newer.document_id == version.document_id,
            newer.status == "ok",
            tuple_(newer.created_at, newer.id) > tuple_(version.created_at, version.id),
        )
        .correlate(version)
        .exists(),
    )


def is_eligible_version(version: VersionEntity) -> ColumnElement[bool]:
    """``version`` is the one version of its document that is searchable.

    The rule of the spec, written once here: the latest ``ok`` version, and only
    when it is canonical (``duplicate_of IS NULL``). A document whose latest
    ``ok`` version is a duplicate has no eligible version at all; its older
    versions are not eligible either, because they are not the latest. The
    indexing pipeline's repository builds its queries on this same function, and
    an integration test there checks that both sides select the same chunks.
    """
    return and_(is_latest_ok_version(version), version.duplicate_of.is_(None))


def _visible_to(user_id: uuid.UUID) -> ColumnElement[bool]:
    return or_(Document.owner_id.is_(None), Document.owner_id == user_id)


class ChunkStore:
    def __init__(self, sessions: ScopedSessions) -> None:
        self._sessions = sessions

    def hydrate(
        self, user_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[ChunkRecord]:
        """The visible, eligible chunks among ``ids``, in the order of ``ids``.

        Unknown ids, chunks of documents the user cannot see, and chunks of a
        version that is not (or no longer) eligible are silently absent. A
        repeated id appears once, at its first position.
        """
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return []
        query = (
            select(
                Chunk.id,
                DocumentVersion.document_id,
                Chunk.version_id,
                Document.title,
                Chunk.heading,
                Chunk.text,
            )
            .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Chunk.id.in_(wanted),
                _visible_to(user_id),
                is_eligible_version(DocumentVersion),
            )
        )
        with self._sessions.scoped(user_id) as session:
            found = {row[0]: ChunkRecord(*row) for row in session.execute(query)}
        return [found[chunk_id] for chunk_id in wanted if chunk_id in found]

    def indexed_state(
        self,
        user_id: uuid.UUID,
        document_ids: Sequence[uuid.UUID],
        index_name: str,
    ) -> dict[uuid.UUID, bool]:
        """Whether each visible document among ``document_ids`` is searchable.

        For a document, take its latest ``ok`` version V and the version whose
        chunks represent it in the index: V itself when canonical, otherwise its
        canonical version ``V.duplicate_of``. The document is indexed when that
        version is eligible (still the latest canonical ``ok`` version of its own
        document, so its chunks are the ones :meth:`hydrate` returns), has
        ``chunked_at`` set, and none of its chunks lacks an entry for
        ``index_name`` (the live collection, ``RagSettings.index_name``: model
        and dimension, so a new dimension reads as not indexed until re-indexed).
        Zero chunks with ``chunked_at`` set counts as indexed: there is nothing
        to wait for.

        Visible documents without an ``ok`` version map to ``False``; unknown or
        invisible ones are absent. One query, whatever the number of documents.
        """
        wanted = list(dict.fromkeys(document_ids))
        if not wanted:
            return {}
        latest = aliased(DocumentVersion, name="latest_version")
        target = aliased(DocumentVersion, name="target_version")
        pending_chunk = (
            select(Chunk.id)
            .where(
                Chunk.version_id == target.id,
                ~select(ChunkIndexEntry.chunk_id)
                .where(
                    ChunkIndexEntry.chunk_id == Chunk.id,
                    ChunkIndexEntry.index_name == index_name,
                )
                .correlate(Chunk)
                .exists(),
            )
            .correlate(target)
            .exists()
        )
        indexed = and_(
            target.id.is_not(None),
            is_eligible_version(target),
            target.chunked_at.is_not(None),
            ~pending_chunk,
        )
        query = (
            select(Document.id, func.coalesce(indexed, False))
            .select_from(Document)
            .outerjoin(
                latest,
                and_(latest.document_id == Document.id, is_latest_ok_version(latest)),
            )
            .outerjoin(
                target, target.id == func.coalesce(latest.duplicate_of, latest.id)
            )
            .where(Document.id.in_(wanted), _visible_to(user_id))
        )
        with self._sessions.scoped(user_id) as session:
            return {
                document_id: bool(state)
                for document_id, state in session.execute(query)
            }
