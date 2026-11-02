"""The ``indexed`` flag: one state query per page, never one per document."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from explorai_backend.repositories.documents import DocumentRow, DocumentsRepository
from explorai_backend.services.documents import DocumentsService, IndexedFlag

USER = uuid.uuid4()
WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def row(document_id: uuid.UUID) -> DocumentRow:
    return DocumentRow(document_id, "t", "arxiv", True, "ready", WHEN)


class RecordingStates:
    def __init__(self, indexed: set[uuid.UUID]) -> None:
        self._indexed = indexed
        self.calls: list[tuple[uuid.UUID, list[uuid.UUID], str]] = []

    def indexed_state(
        self,
        user_id: uuid.UUID,
        document_ids: Sequence[uuid.UUID],
        index_name: str,
    ) -> dict[uuid.UUID, bool]:
        self.calls.append((user_id, list(document_ids), index_name))
        return {d: d in self._indexed for d in document_ids}


class Rows:
    """A repository stand-in returning fixed rows for list and get."""

    def __init__(self, rows: list[DocumentRow]) -> None:
        self._rows = rows

    def list(self, *args: object) -> list[DocumentRow]:
        return self._rows

    def get(self, user_id: uuid.UUID, document_id: uuid.UUID) -> DocumentRow | None:
        return next((r for r in self._rows if r.id == document_id), None)


def test_a_page_costs_one_state_query() -> None:
    ids = [uuid.uuid4() for _ in range(4)]
    states = RecordingStates({ids[1], ids[3]})
    repository = cast(DocumentsRepository, Rows([row(i) for i in ids]))
    service = DocumentsService(repository, IndexedFlag(states, "chunks_m_16"))

    # limit 3: the repository's extra row tells "more pages" and is not flagged.
    page = service.list(USER, "all", 3, None)

    assert [item.indexed for item in page.items] == [False, True, False]
    assert states.calls == [(USER, ids[:3], "chunks_m_16")]


def test_one_document_is_flagged_too() -> None:
    document = uuid.uuid4()
    states = RecordingStates({document})
    repository = cast(DocumentsRepository, Rows([row(document)]))
    service = DocumentsService(repository, IndexedFlag(states, "chunks_m_16"))

    assert service.get(USER, document).indexed is True


def test_absent_state_reads_as_not_indexed() -> None:
    class Empty:
        def indexed_state(
            self, user_id: uuid.UUID, ids: Sequence[uuid.UUID], index_name: str
        ) -> dict[uuid.UUID, bool]:
            return {}

    flagged = IndexedFlag(Empty(), "chunks_m_16").apply(USER, [row(uuid.uuid4())])

    assert [item.indexed for item in flagged] == [False]


def test_an_empty_page_runs_no_query() -> None:
    states = RecordingStates(set())

    assert IndexedFlag(states, "chunks_m_16").apply(USER, []) == []
    assert states.calls == []
