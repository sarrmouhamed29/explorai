"""User isolation across the whole retrieval path (Review Focus 1 of the RAG plan).

Real PostgreSQL (RLS) and Qdrant, fake models, documents indexed by the real
indexing function. Alice and Bob each own a private document and both can read a
public one. Bob's text is full of words that appear nowhere else (``MARKERS``),
and the questions below are built from them, so any leak of his chunks would be
visible by id and by string at every stage: retrieval candidates, the prompt the
generator receives, and the final answer.

Three independent defences stand between Alice and Bob's text:

1. the Qdrant filter built by ``explorai_ai.scope.build_scope_filter``;
2. the explicit owner condition in ``ChunkStore.hydrate``'s query;
3. the Row-Level Security policies (the query runs as ``explorai_app``).

Each test asserts a positive control too (Alice does get her own and the public
chunks, Bob does get his), so an always-empty result cannot pass.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from qdrant_client.models import Filter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai import scope as scope_module
from explorai_ai.config import RagSettings
from explorai_ai.index import QdrantIndex
from explorai_ai.models import AnswerResult
from explorai_ai.retriever import RetrievalResult
from explorai_ai.scope import Scope, build_scope_filter
from explorai_ai.store import ChunkRecord, ChunkStore
from explorai_db.models import Chunk
from explorai_db.rls import ScopedSessions
from integration.helpers import (
    RecordingGenerator,
    add_user,
    chunk_ids_of,
    document_with_text,
    index_everything,
    rag_stack,
    raw_search,
)

pytestmark = pytest.mark.integration

BASE = datetime(2026, 1, 1, tzinfo=UTC)

ALICE_TITLE = "Alice photonics notes"
ALICE_TEXT = (
    "Photonic lattices guide entangled photons through silicon waveguides "
    "at room temperature."
)
PUBLIC_TITLE = "Alpine lakes primer"
PUBLIC_TEXT = (
    "Glacier meltwater carries fine sediment that colours alpine lakes "
    "turquoise every summer."
)
BOB_TITLE = "Bob zymurgy notebook"
BOB_TEXT = (
    "Quokka zymurgy ferments purple carrots with juniper berries for twelve "
    "weeks in a cold cellar."
)
# Words of Bob's title and text that occur in no other document.
MARKERS = ("quokka", "zymurgy", "juniper", "carrots", "cellar", "bob")

ALICE_QUESTION = "How do photonic lattices guide entangled photons?"
PUBLIC_QUESTION = "Why does glacier meltwater colour alpine lakes turquoise?"
# Built from Bob's words: with the fake reranker (share of the question's words
# found in a chunk) they score 7/11, 7/9 and 2/2 on Bob's chunk, 0 elsewhere.
BOB_QUESTIONS = (
    "How long does quokka zymurgy ferment purple carrots with juniper berries?",
    "Which cold cellar keeps juniper berries for twelve weeks?",
    "quokka zymurgy",
)
# Half Alice's words, half Bob's: 5/13 on Alice's chunk (over the 0.2 rerank
# threshold and the fake generator's 0.3), 4/13 on Bob's. Alice's search does
# reach the generator with it, so the prompt check below is never vacuous.
MIXED_QUESTION = (
    "Do photonic lattices guide entangled photons, or does quokka zymurgy "
    "ferment juniper berries?"
)


@dataclass(frozen=True)
class Corpus:
    alice: uuid.UUID
    bob: uuid.UUID
    alice_doc: uuid.UUID
    bob_doc: uuid.UUID
    public_doc: uuid.UUID
    alice_chunks: set[uuid.UUID]
    bob_chunks: set[uuid.UUID]
    public_chunks: set[uuid.UUID]


@pytest.fixture
def corpus(
    clean_database: None,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> Corpus:
    alice = add_user(session_factory, "alice@example.org")
    bob = add_user(session_factory, "bob@example.org")
    alice_doc = document_with_text(
        session_factory, alice, ALICE_TITLE, ALICE_TEXT, BASE
    )
    bob_doc = document_with_text(session_factory, bob, BOB_TITLE, BOB_TEXT, BASE)
    public_doc = document_with_text(
        session_factory, None, PUBLIC_TITLE, PUBLIC_TEXT, BASE
    )
    counters = index_everything(session_factory, qdrant_index, rag_settings)
    assert counters["chunked"] == 3
    assert counters["embedded"] == 3
    corpus = Corpus(
        alice=alice,
        bob=bob,
        alice_doc=alice_doc,
        bob_doc=bob_doc,
        public_doc=public_doc,
        alice_chunks=chunk_ids_of(session_factory, alice_doc),
        bob_chunks=chunk_ids_of(session_factory, bob_doc),
        public_chunks=chunk_ids_of(session_factory, public_doc),
    )
    # One chunk each (one short section), so set comparisons below are exact.
    assert len(corpus.alice_chunks) == len(corpus.bob_chunks) == 1
    assert len(corpus.public_chunks) == 1
    return corpus


def leaked_markers(*texts: str | None) -> list[str]:
    joined = " ".join(text for text in texts if text).lower()
    return [marker for marker in MARKERS if marker in joined]


def assert_retrieval_has_nothing_of_bob(
    result: RetrievalResult, corpus: Corpus
) -> None:
    for candidate in result.candidates:
        assert candidate.chunk_id not in corpus.bob_chunks
        assert candidate.document_id != corpus.bob_doc
        assert candidate.document_title != BOB_TITLE
        assert (
            leaked_markers(candidate.text, candidate.heading, candidate.document_title)
            == []
        )


def assert_prompts_have_nothing_of_bob(generator: RecordingGenerator) -> None:
    for _question, sources in generator.answers:
        for source in sources:
            assert leaked_markers(source.text, source.title, source.heading) == []
            assert source.title != BOB_TITLE


def assert_answer_has_nothing_of_bob(answer: AnswerResult, corpus: Corpus) -> None:
    assert leaked_markers(answer.answer) == []
    for citation in answer.citations:
        assert citation.chunk_id not in corpus.bob_chunks
        assert citation.document_id != corpus.bob_doc
        assert citation.document_title != BOB_TITLE
        assert leaked_markers(citation.quote, citation.document_title) == []


def ids(result: RetrievalResult) -> set[uuid.UUID]:
    return {candidate.chunk_id for candidate in result.candidates}


def test_search_never_returns_another_owners_chunks(
    corpus: Corpus,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    alice = rag_stack(session_factory, qdrant_index, rag_settings)
    for question in (*BOB_QUESTIONS, MIXED_QUESTION):
        result = alice.retriever.retrieve(corpus.alice, question)
        # Positive control: the search ran and returned what Alice may read
        # (the dense prefetch returns every visible point of this tiny corpus).
        assert ids(result) == corpus.alice_chunks | corpus.public_chunks, question
        assert_retrieval_has_nothing_of_bob(result, corpus)

        answer = alice.pipeline.ask(corpus.alice, question)
        assert_prompts_have_nothing_of_bob(alice.generator)
        assert_answer_has_nothing_of_bob(answer, corpus)

    # Bob's questions score 0 on everything Alice can see: refused before any
    # generation. The mixed one reached the generator and was answered from
    # Alice's own chunk.
    assert len(alice.generator.answers) == 1
    question, sources = alice.generator.answers[0]
    assert question == MIXED_QUESTION
    assert ALICE_TEXT in {source.text for source in sources}
    mixed = alice.pipeline.ask(corpus.alice, MIXED_QUESTION)
    assert mixed.status == "answered"
    assert {c.document_id for c in mixed.citations} == {corpus.alice_doc}

    # Positive control on Bob's side: the same questions do match his chunk
    # strongly, so Alice's empty-handed answers are isolation, not bad questions.
    bob = rag_stack(session_factory, qdrant_index, rag_settings)
    for question in BOB_QUESTIONS:
        result = bob.retriever.retrieve(corpus.bob, question)
        assert result.candidates[0].chunk_id in corpus.bob_chunks, question
        answer = bob.pipeline.ask(corpus.bob, question)
        assert answer.status == "answered", question
        assert {c.document_id for c in answer.citations} == {corpus.bob_doc}


class SpyStore(ChunkStore):
    """The real store, remembering which ids Qdrant handed it to re-read."""

    def __init__(self, sessions: ScopedSessions) -> None:
        super().__init__(sessions)
        self.requested: set[uuid.UUID] = set()

    def hydrate(
        self, user_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[ChunkRecord]:
        self.requested.update(ids)
        return super().hydrate(user_id, ids)


def match_everything(
    user_id: uuid.UUID, scope: Scope = "all", document_id: uuid.UUID | None = None
) -> Filter:
    """A filter without conditions: Qdrant returns every owner's points."""
    del user_id, scope, document_id
    return Filter()


def test_rls_still_stops_foreign_chunks_when_the_qdrant_filter_is_removed(
    monkeypatch: pytest.MonkeyPatch,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    question = BOB_QUESTIONS[0]

    # Defence 1 on its own: the real filter keeps Bob's point out of Qdrant's
    # answer, while the same search without it does return that point.
    assert raw_search(
        qdrant_index, rag_settings, question, build_scope_filter(corpus.alice)
    ).isdisjoint(corpus.bob_chunks)
    assert corpus.bob_chunks <= raw_search(
        qdrant_index, rag_settings, question, Filter()
    )

    # Defence 3 on its own, without the store's explicit owner condition: under
    # Alice's identity RLS hides Bob's chunk row; under Bob's it is there.
    sessions = ScopedSessions(session_factory)
    by_id = select(Chunk.id).where(Chunk.id.in_(list(corpus.bob_chunks)))
    with sessions.scoped(corpus.alice) as session:
        assert list(session.scalars(by_id)) == []
    with sessions.scoped(corpus.bob) as session:
        assert set(session.scalars(by_id)) == corpus.bob_chunks

    # The whole path with defence 1 removed. The retriever looks the function up
    # on the module at each call (``scoping.build_scope_filter``), so this patch
    # reaches it.
    monkeypatch.setattr(scope_module, "build_scope_filter", match_everything)
    spy = SpyStore(sessions)
    alice = rag_stack(session_factory, qdrant_index, rag_settings, store=spy)
    for asked in (*BOB_QUESTIONS, MIXED_QUESTION):
        result = alice.retriever.retrieve(corpus.alice, asked)
        assert_retrieval_has_nothing_of_bob(result, corpus)
        # Alice's own and the public chunks still come through.
        assert ids(result) == corpus.alice_chunks | corpus.public_chunks, asked
        answer = alice.pipeline.ask(corpus.alice, asked)
        assert_prompts_have_nothing_of_bob(alice.generator)
        assert_answer_has_nothing_of_bob(answer, corpus)

    # The patch really took effect: Qdrant handed Bob's chunk id to the store,
    # and the PostgreSQL re-read is what dropped it.
    assert corpus.bob_chunks <= spy.requested
    # The mixed question reached the generator, with Alice's text and none of Bob's.
    assert [q for q, _ in alice.generator.answers] == [MIXED_QUESTION]
    assert ALICE_TEXT in {s.text for s in alice.generator.answers[0][1]}


def test_public_corpus_is_searchable_by_everyone_and_private_only_by_owner(
    corpus: Corpus,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    stack = rag_stack(session_factory, qdrant_index, rag_settings)
    retrieve = stack.retriever.retrieve

    # The public document answers everyone.
    for user in (corpus.alice, corpus.bob):
        result = retrieve(user, PUBLIC_QUESTION)
        assert result.candidates[0].chunk_id in corpus.public_chunks
        answer = stack.pipeline.ask(user, PUBLIC_QUESTION)
        assert answer.status == "answered"
        assert {c.document_id for c in answer.citations} == {corpus.public_doc}
        assert answer.citations[0].document_title == PUBLIC_TITLE

    # A private document answers its owner only.
    own = stack.pipeline.ask(corpus.alice, ALICE_QUESTION)
    assert own.status == "answered"
    assert {c.document_id for c in own.citations} == {corpus.alice_doc}
    foreign = retrieve(corpus.bob, ALICE_QUESTION)
    assert ids(foreign) == corpus.bob_chunks | corpus.public_chunks
    assert stack.pipeline.ask(corpus.bob, ALICE_QUESTION).status == (
        "insufficient_evidence"
    )

    # Scopes: "public" is the public corpus only, "mine" the user's own only.
    assert ids(retrieve(corpus.alice, ALICE_QUESTION, "public")) == (
        corpus.public_chunks
    )
    assert ids(retrieve(corpus.alice, PUBLIC_QUESTION, "mine")) == corpus.alice_chunks
    assert ids(retrieve(corpus.bob, BOB_QUESTIONS[0], "mine")) == corpus.bob_chunks
    assert ids(retrieve(corpus.bob, BOB_QUESTIONS[0], "public")) == (
        corpus.public_chunks
    )

    # Naming someone else's document id does not open it.
    scopes: tuple[Scope, ...] = ("all", "mine", "public")
    for scope in scopes:
        narrowed = retrieve(corpus.alice, BOB_QUESTIONS[0], scope, corpus.bob_doc)
        assert narrowed.candidates == [], scope
    # Positive control: narrowing to a visible document works.
    assert ids(retrieve(corpus.alice, ALICE_QUESTION, "all", corpus.alice_doc)) == (
        corpus.alice_chunks
    )
