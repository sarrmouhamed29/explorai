"""The only module that writes to the database.

Every public method is one transaction, so a failure never leaves partial rows.
"""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, exists, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from explorai_ai.chunking import ChunkText, SectionInput
from explorai_ai.store import is_eligible_version
from explorai_db.models import (
    Chunk,
    ChunkIndexEntry,
    Document,
    DocumentVersion,
    IngestionRun,
    RawArtifact,
    Section,
    Source,
)
from explorai_pipelines.dedup import text_hash
from explorai_pipelines.extract.models import ExtractedSection


@dataclass(frozen=True)
class ArtifactRef:
    id: uuid.UUID
    source_id: uuid.UUID
    external_id: str
    url: str
    owner_id: uuid.UUID | None
    storage_key: str


@dataclass(frozen=True)
class SavedVersion:
    version_id: uuid.UUID
    document_created: bool


@dataclass(frozen=True)
class VersionToChunk:
    version_id: uuid.UUID
    document_id: uuid.UUID
    owner_id: uuid.UUID | None
    sections: list[SectionInput]


@dataclass(frozen=True)
class PendingChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    owner_id: uuid.UUID | None
    heading: str | None
    text: str


# The searchable version of a document: the latest ``ok`` one, when canonical.
# Written once, in ``explorai_ai.store`` (the API reads chunks through it), and
# reused here by every indexing query, so the pipeline indexes exactly what the
# API will return. An integration test checks both sides select the same chunks.
def _eligible() -> ColumnElement[bool]:
    return is_eligible_version(DocumentVersion)


def _owner_is(
    column: InstrumentedAttribute[uuid.UUID | None], owner_id: uuid.UUID | None
) -> ColumnElement[bool]:
    if owner_id is None:
        return column.is_(None)
    return column == owner_id


def _same_document(
    source_id: uuid.UUID, external_id: str, owner_id: uuid.UUID | None
) -> ColumnElement[bool]:
    return and_(
        Document.source_id == source_id,
        Document.external_id == external_id,
        _owner_is(Document.owner_id, owner_id),
    )


class Repository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    # locks

    @contextmanager
    def advisory_lock(self, key: int) -> Iterator[bool]:
        """Try a session-level PostgreSQL advisory lock; yield whether it was taken.

        The lock lives on a dedicated connection held for the whole ``with`` block,
        so the repository's short transactions (on other connections) neither
        hold nor lose it. It is released on exit, and by PostgreSQL itself if the
        process dies. A transaction-level lock would end with the first commit.
        """
        engine = self._factory.kw["bind"]
        with engine.connect() as connection:
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                ).scalar()
            )
            connection.commit()  # do not sit idle in a transaction while working
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                    connection.commit()

    # sources and runs

    def get_or_create_source(
        self, kind: str, name: str, config: dict[str, Any]
    ) -> uuid.UUID:
        with self._factory.begin() as session:
            session.execute(
                pg_insert(Source)
                .values(id=uuid.uuid4(), kind=kind, name=name, config=config)
                .on_conflict_do_nothing(index_elements=["kind", "name"])
            )
            return session.scalars(
                select(Source.id).where(Source.kind == kind, Source.name == name)
            ).one()

    def start_run(self, source_id: uuid.UUID | None) -> uuid.UUID:
        with self._factory.begin() as session:
            run = IngestionRun(source_id=source_id, status="running")
            session.add(run)
            session.flush()
            return run.id

    def finish_run(
        self,
        run_id: uuid.UUID,
        counters: dict[str, int],
        errors: list[str],
        status: str,
    ) -> None:
        with self._factory.begin() as session:
            session.execute(
                update(IngestionRun)
                .where(IngestionRun.id == run_id)
                .values(
                    finished_at=datetime.now(UTC),
                    status=status,
                    counters=counters,
                    errors=errors,
                )
            )

    # artifacts

    def find_artifact(
        self,
        source_id: uuid.UUID,
        external_id: str,
        owner_id: uuid.UUID | None,
        sha256: str,
    ) -> uuid.UUID | None:
        with self._factory() as session:
            return session.scalars(
                select(RawArtifact.id).where(
                    RawArtifact.source_id == source_id,
                    RawArtifact.external_id == external_id,
                    _owner_is(RawArtifact.owner_id, owner_id),
                    RawArtifact.sha256 == sha256,
                )
            ).one_or_none()

    def store_artifact(
        self,
        *,
        source_id: uuid.UUID,
        external_id: str,
        url: str,
        sha256: str,
        storage_key: str,
        owner_id: uuid.UUID | None,
    ) -> uuid.UUID:
        with self._factory.begin() as session:
            artifact = RawArtifact(
                source_id=source_id,
                external_id=external_id,
                url=url,
                sha256=sha256,
                storage_key=storage_key,
                owner_id=owner_id,
            )
            session.add(artifact)
            session.flush()
            return artifact.id

    def has_version(self, artifact_id: uuid.UUID, extractor_version: str) -> bool:
        with self._factory() as session:
            return bool(
                session.scalar(
                    select(
                        exists().where(
                            DocumentVersion.artifact_id == artifact_id,
                            DocumentVersion.extractor_version == extractor_version,
                        )
                    )
                )
            )

    def artifacts_missing_version(self, extractor_version: str) -> list[ArtifactRef]:
        """Artifacts with no version by ``extractor_version``, whatever their kind.

        A PDF never gets an ``html-*`` version (and the reverse), so for one
        extractor this is most of the corpus: fine for the nightly catch-up, too
        much for a pass every 30 seconds (see ``artifacts_without_any_version``).
        """
        return self._artifact_refs(
            ~exists().where(
                DocumentVersion.artifact_id == RawArtifact.id,
                DocumentVersion.extractor_version == extractor_version,
            )
        )

    def artifacts_without_any_version(self) -> list[ArtifactRef]:
        """Artifacts no extractor has processed yet: fresh uploads, failed runs."""
        return self._artifact_refs(
            ~exists().where(DocumentVersion.artifact_id == RawArtifact.id)
        )

    def _artifact_refs(self, condition: ColumnElement[bool]) -> list[ArtifactRef]:
        with self._factory() as session:
            rows = session.scalars(
                select(RawArtifact).where(condition).order_by(RawArtifact.fetched_at)
            ).all()
            return [
                ArtifactRef(
                    id=r.id,
                    source_id=r.source_id,
                    external_id=r.external_id,
                    url=r.url,
                    owner_id=r.owner_id,
                    storage_key=r.storage_key,
                )
                for r in rows
            ]

    def referenced_storage_keys(self) -> set[str]:
        with self._factory() as session:
            return set(session.scalars(select(RawArtifact.storage_key)).all())

    # versions

    def save_version(
        self,
        *,
        source_id: uuid.UUID,
        external_id: str,
        owner_id: uuid.UUID | None,
        title: str | None,
        extracted_title: str | None,
        source_metadata: dict[str, str] | None,
        artifact_id: uuid.UUID,
        extractor_version: str,
        status: str,
        text_hash: str | None,
        signature: bytes | None,
        duplicate_of: uuid.UUID | None,
        sections: list[ExtractedSection],
    ) -> SavedVersion:
        """Upsert the document, add the version and its sections, all or nothing.

        ``title`` and ``source_metadata`` come from the source and win; the title
        extracted from the file only fills a document that has none.
        """
        with self._factory.begin() as session:
            document = session.scalars(
                select(Document).where(_same_document(source_id, external_id, owner_id))
            ).one_or_none()
            created = document is None
            if document is None:
                document = Document(
                    source_id=source_id,
                    external_id=external_id,
                    owner_id=owner_id,
                    title=title or extracted_title,
                    source_metadata=source_metadata or None,
                )
                session.add(document)
                session.flush()
            else:
                if title:
                    document.title = title
                elif document.title is None and extracted_title:
                    document.title = extracted_title
                if source_metadata:
                    document.source_metadata = source_metadata
            version = DocumentVersion(
                document_id=document.id,
                artifact_id=artifact_id,
                extractor_version=extractor_version,
                status=status,
                text_hash=text_hash,
                minhash_signature=signature,
                duplicate_of=duplicate_of,
            )
            session.add(version)
            session.flush()
            session.add_all(
                Section(
                    version_id=version.id,
                    ordinal=ordinal,
                    heading=section.heading,
                    text=section.text,
                )
                for ordinal, section in enumerate(sections)
            )
            session.flush()  # surface constraint errors inside the transaction
            return SavedVersion(version_id=version.id, document_created=created)

    # duplicate detection

    def find_exact_duplicate(
        self,
        text_hash: str,
        source_id: uuid.UUID,
        external_id: str,
        owner_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        """Oldest canonical ok version with this hash, same owner, other document.

        Scoped by owner so that a private document never becomes the canonical
        version of a public one (or of another user's).
        """
        with self._factory() as session:
            return session.scalars(
                select(DocumentVersion.id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.text_hash == text_hash,
                    DocumentVersion.status == "ok",
                    DocumentVersion.duplicate_of.is_(None),
                    _owner_is(Document.owner_id, owner_id),
                    or_(
                        Document.source_id != source_id,
                        Document.external_id != external_id,
                    ),
                )
                .order_by(DocumentVersion.created_at)
                .limit(1)
            ).one_or_none()

    def load_signatures(self, owner_id: uuid.UUID | None) -> list[tuple[str, bytes]]:
        """Signatures of the canonical versions visible to one owner's dedup."""
        with self._factory() as session:
            rows = session.execute(
                select(DocumentVersion.id, DocumentVersion.minhash_signature)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    _owner_is(Document.owner_id, owner_id),
                    DocumentVersion.status == "ok",
                    DocumentVersion.duplicate_of.is_(None),
                    DocumentVersion.minhash_signature.is_not(None),
                )
            ).all()
            return [(str(row[0]), row[1]) for row in rows if row[1] is not None]

    def document_version_ids(
        self, source_id: uuid.UUID, external_id: str, owner_id: uuid.UUID | None
    ) -> frozenset[str]:
        with self._factory() as session:
            ids = session.scalars(
                select(DocumentVersion.id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(_same_document(source_id, external_id, owner_id))
            ).all()
            return frozenset(str(i) for i in ids)

    # chunks and the vector index (all through the one eligibility rule)

    def versions_to_chunk(self, limit: int) -> list[VersionToChunk]:
        """Eligible versions not cut into chunks yet, oldest first, with sections."""
        with self._factory() as session:
            rows = session.execute(
                select(
                    DocumentVersion.id, DocumentVersion.document_id, Document.owner_id
                )
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(_eligible(), DocumentVersion.chunked_at.is_(None))
                .order_by(DocumentVersion.created_at, DocumentVersion.id)
                .limit(limit)
            ).all()
            if not rows:
                return []
            sections: dict[uuid.UUID, list[SectionInput]] = {row[0]: [] for row in rows}
            for version_id, heading, body in session.execute(
                select(Section.version_id, Section.heading, Section.text)
                .where(Section.version_id.in_(list(sections)))
                .order_by(Section.version_id, Section.ordinal)
            ):
                sections[version_id].append(SectionInput(heading, body))
            return [
                VersionToChunk(version_id, document_id, owner_id, sections[version_id])
                for version_id, document_id, owner_id in rows
            ]

    def save_chunks(self, version_id: uuid.UUID, chunks: Sequence[ChunkText]) -> None:
        """Store the chunks of a version and mark it chunked, all or nothing.

        Zero chunks still sets ``chunked_at``, so the version is not chunked again.
        The marker is set first, conditionally: if it was already set (a version
        chunked by an earlier pass) nothing is inserted, so a replay can neither
        duplicate chunks nor hit the ``(version_id, ordinal)`` constraint.
        """
        with self._factory.begin() as session:
            marked = session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.chunked_at.is_(None),
                )
                .values(chunked_at=datetime.now(UTC))
                .returning(DocumentVersion.id)
            ).first()
            if marked is None:
                return
            session.add_all(
                Chunk(
                    version_id=version_id,
                    ordinal=chunk.ordinal,
                    heading=chunk.heading,
                    text=chunk.text,
                    word_count=chunk.word_count,
                    text_hash=text_hash(chunk.text),
                )
                for chunk in chunks
            )
            session.flush()

    def pending_chunks(self, index_name: str, limit: int) -> list[PendingChunk]:
        """Chunks of eligible versions with no entry for the index ``index_name``."""
        recorded = (
            select(ChunkIndexEntry.chunk_id)
            .where(
                ChunkIndexEntry.chunk_id == Chunk.id,
                ChunkIndexEntry.index_name == index_name,
            )
            .correlate(Chunk)
            .exists()
        )
        with self._factory() as session:
            rows = session.execute(
                select(
                    Chunk.id,
                    DocumentVersion.document_id,
                    Chunk.version_id,
                    Document.owner_id,
                    Chunk.heading,
                    Chunk.text,
                )
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(_eligible(), ~recorded)
                .order_by(DocumentVersion.created_at, Chunk.version_id, Chunk.ordinal)
                .limit(limit)
            ).all()
            return [PendingChunk(*row) for row in rows]

    def record_entries(self, chunk_ids: Sequence[uuid.UUID], index_name: str) -> None:
        """Mark chunks as in the index; recording one twice is a no-op."""
        if not chunk_ids:
            return
        now = datetime.now(UTC)
        with self._factory.begin() as session:
            session.execute(
                pg_insert(ChunkIndexEntry)
                .values(
                    [
                        {
                            "chunk_id": chunk_id,
                            "index_name": index_name,
                            "indexed_at": now,
                        }
                        for chunk_id in chunk_ids
                    ]
                )
                .on_conflict_do_nothing(index_elements=["chunk_id", "index_name"])
            )

    def clear_entries(self, index_name: str) -> int:
        """Forget every entry of an index, so all its chunks are pending again.

        For an index whose collection no longer exists (Qdrant data lost or
        deleted): the entries would otherwise claim chunks that are not there.
        Returns how many entries were removed.
        """
        with self._factory.begin() as session:
            removed = session.execute(
                delete(ChunkIndexEntry)
                .where(ChunkIndexEntry.index_name == index_name)
                .returning(ChunkIndexEntry.chunk_id)
            ).all()
            return len(removed)

    def eligible_chunk_ids(self) -> set[uuid.UUID]:
        """Ids of every chunk of an eligible version: what the index should hold."""
        with self._factory() as session:
            return set(
                session.scalars(
                    select(Chunk.id)
                    .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                    .where(_eligible())
                ).all()
            )
