"""End-to-end behaviour of the RAG path: real PostgreSQL, Redis db 15, Qdrant.

The models are the deterministic fakes of ``explorai_ai`` (no network, no key),
so every expected outcome below is computed from their rules: the fake reranker
scores a chunk by the share of the question's words it contains (refusal under
0.2), and the fake generator answers from the best source when that share
reaches 0.3, quoting its first sentence.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.citations import normalise_for_match
from explorai_ai.config import RagSettings
from explorai_ai.index import QdrantIndex
from explorai_ai.scope import build_scope_filter
from explorai_backend.config import Settings
from explorai_db.blobs import LocalBlobStore
from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.indexing import prune_index
from explorai_pipelines.ingest import reextract
from explorai_pipelines.repository import Repository
from integration.helpers import (
    add_document,
    add_user,
    add_version,
    chunk_ids_of,
    document_with_text,
    index_everything,
    login_as,
    login_as_existing,
    rag_stack,
    raw_search,
)

pytestmark = pytest.mark.integration

ASK = "/api/v1/ask"
DOCUMENTS = "/api/v1/documents"
BASE = datetime(2026, 1, 1, tzinfo=UTC)
PAPER = (
    Path(__file__).resolve().parents[3]
    / "pipelines"
    / "tests"
    / "fixtures"
    / "annotated"
    / "2610.40627.pdf"
)

ALICE_TEXT = (
    "Photonic lattices guide entangled photons through silicon waveguides "
    "at room temperature."
)
PUBLIC_TEXT = (
    "Glacier meltwater carries fine sediment that colours alpine lakes "
    "turquoise every summer."
)
BOB_TEXT = (
    "Quokka zymurgy ferments purple carrots with juniper berries for twelve "
    "weeks in a cold cellar."
)
ALICE_QUESTION = "How do photonic lattices guide entangled photons?"  # 5/7 on Alice
BOB_QUESTION = "How long does quokka zymurgy ferment purple carrots?"
# No word in common with any document above (not even a stop word): 0 everywhere.
UNANSWERABLE = "Which volcano erupted beneath the Antarctic ice sheet?"


def ask(client: TestClient, question: str, **extra: object) -> dict[str, Any]:
    response = client.post(ASK, json={"question": question, **extra})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def sections_of(client: TestClient, document_id: str) -> list[str]:
    texts: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(f"{DOCUMENTS}/{document_id}/sections", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        texts.extend(item["text"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return texts


def test_answer_flow_over_http(
    rag_app: FastAPI,
    client: TestClient,
    settings: Settings,
    database_url: str,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    login_as(client, "alice@example.org")
    uploaded = client.post(
        DOCUMENTS,
        params={"filename": "shallow-defend.pdf"},
        content=PAPER.read_bytes(),
        headers={"Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 202, uploaded.text
    document = uploaded.json()
    assert (document["status"], document["indexed"]) == ("processing", False)
    document_id = document["id"]

    # process-pending: extract the stored upload (what the flow calls).
    pipeline_settings = PipelineSettings(
        database_url=database_url,  # type: ignore[arg-type]
        blob_dir=settings.blob_dir,
        contact_email="me@example.org",
    )
    repo = Repository(session_factory)
    extracted = reextract(
        repo=repo,
        store=LocalBlobStore(settings.blob_dir),
        settings=pipeline_settings,
        only_unversioned=True,
    )
    assert extracted["new_version"] == 1, extracted
    assert extracted["failed"] == extracted["unextractable"] == 0, extracted
    ready = client.get(f"{DOCUMENTS}/{document_id}").json()
    assert (ready["status"], ready["indexed"]) == ("ready", False)

    # index-pending: chunk, embed, upsert, record (what the flow calls).
    counters = index_everything(session_factory, qdrant_index, rag_settings)
    assert counters["chunked"] == 1
    assert counters["embedded"] > 0
    assert client.get(f"{DOCUMENTS}/{document_id}").json()["indexed"] is True

    # Words of the paper ("A third probe runs the decoding-parameter exploit,
    # which defeats shallow alignment by sampling alone."): 13 of the question's
    # 14 words are in that sentence ("that" is not), well over the fake
    # thresholds, so its chunk is the source the fake generator quotes.
    body = ask(
        client,
        "Which probe runs the decoding-parameter exploit that defeats shallow "
        "alignment by sampling alone?",
    )
    assert body["status"] == "answered", body
    assert body["answer"]
    citations = body["citations"]
    assert citations
    sections = [normalise_for_match(text) for text in sections_of(client, document_id)]
    for citation in citations:
        assert citation["document_id"] == document_id
        assert citation["document_title"] == "shallow-defend"
        quote = normalise_for_match(citation["quote"])
        assert any(quote in section for section in sections), citation


def test_question_without_answer_is_refused_without_generation(
    clean_database: None,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    alice = add_user(session_factory, "alice@example.org")
    document_with_text(session_factory, alice, "mine", ALICE_TEXT, BASE)
    document_with_text(session_factory, None, "public", PUBLIC_TEXT, BASE)
    index_everything(session_factory, qdrant_index, rag_settings)
    stack = rag_stack(session_factory, qdrant_index, rag_settings)

    # There are candidates (level 1 is the threshold, not an empty search)...
    retrieved = stack.retriever.retrieve(alice, UNANSWERABLE)
    assert len(retrieved.candidates) == 2
    assert retrieved.best_score == 0.0
    # ...and the refusal comes before the generator is ever called.
    answer = stack.pipeline.ask(alice, UNANSWERABLE)
    assert (answer.status, answer.answer, answer.citations) == (
        "insufficient_evidence",
        None,
        [],
    )
    assert (answer.usage.input_tokens, answer.usage.output_tokens) == (0, 0)
    assert stack.generator.answers == []

    # Positive control: the same stack does generate for an answerable question.
    assert stack.pipeline.ask(alice, ALICE_QUESTION).status == "answered"
    assert len(stack.generator.answers) == 1


def test_deleted_document_never_yields_text_and_prune_cleans_up(
    rag_app: FastAPI,
    client: TestClient,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    alice = login_as(client, "alice@example.org")
    public = document_with_text(session_factory, None, "public", PUBLIC_TEXT, BASE)
    mine = add_document(session_factory, alice, title="mine", created_at=BASE)
    old_text = (
        "Superseded draft notes claim photonic lattices dissolve in warm "
        "brine overnight."
    )
    old_question = "Do photonic lattices dissolve in warm brine overnight?"
    add_version(session_factory, mine, "ok", BASE, texts=(old_text,))
    index_everything(session_factory, qdrant_index, rag_settings)
    old_chunks = chunk_ids_of(session_factory, mine)
    # A newer version replaces it; its chunk is indexed, the old point stays.
    add_version(
        session_factory, mine, "ok", BASE + timedelta(days=1), texts=(ALICE_TEXT,)
    )
    index_everything(session_factory, qdrant_index, rag_settings)
    mine_chunks = chunk_ids_of(session_factory, mine)
    new_chunks = mine_chunks - old_chunks
    public_chunks = chunk_ids_of(session_factory, public)
    assert len(old_chunks) == len(new_chunks) == len(public_chunks) == 1
    stack = rag_stack(session_factory, qdrant_index, rag_settings)

    # A non-latest version: Qdrant still returns its point under Alice's own
    # filter, and the PostgreSQL re-read drops it.
    assert old_chunks <= raw_search(
        qdrant_index, rag_settings, old_question, build_scope_filter(alice)
    )
    stale = stack.retriever.retrieve(alice, old_question)
    assert {c.chunk_id for c in stale.candidates} == new_chunks | public_chunks
    assert all("brine" not in c.text for c in stale.candidates)
    # Over HTTP: 2/8 of its words on the new chunk passes the rerank threshold
    # but not the fake generator's 0.3, so a refusal, and never the old text.
    old = ask(client, old_question)
    assert old["status"] == "insufficient_evidence"
    assert "brine" not in str(old)

    before = ask(client, ALICE_QUESTION)
    assert before["status"] == "answered"
    assert {c["chunk_id"] for c in before["citations"]} == {
        str(chunk) for chunk in new_chunks
    }

    deleted = client.delete(f"{DOCUMENTS}/{mine}")
    assert deleted.status_code == 204, deleted.text
    assert chunk_ids_of(session_factory, mine) == set()
    # The points are still in Qdrant (derived data, pruned later)...
    collection = rag_settings.index_name
    assert mine_chunks <= set(qdrant_index.point_ids(collection))
    assert mine_chunks <= raw_search(
        qdrant_index, rag_settings, ALICE_QUESTION, build_scope_filter(alice)
    )
    # ...yet they never turn into text again.
    after = client.post(ASK, json={"question": ALICE_QUESTION})
    assert after.status_code == 200, after.text
    assert after.json()["status"] == "insufficient_evidence"
    assert after.json()["citations"] == []
    assert "photonic" not in after.text.lower()
    gone = stack.retriever.retrieve(alice, ALICE_QUESTION)
    assert {c.chunk_id for c in gone.candidates} == public_chunks

    pruned = prune_index(Repository(session_factory), qdrant_index, rag_settings)
    assert pruned == {"pruned": 2, "skipped": 0}
    assert set(qdrant_index.point_ids(collection)) == public_chunks
    again = prune_index(Repository(session_factory), qdrant_index, rag_settings)
    assert again == {"pruned": 0, "skipped": 0}


def test_empty_corpus_gives_insufficient_evidence_not_500(
    rag_app: FastAPI,
    client: TestClient,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    bob = login_as(client, "bob@example.org")
    alice = login_as(client, "alice@example.org")
    stack = rag_stack(session_factory, qdrant_index, rag_settings)

    def assert_refused(question: str) -> None:
        assert stack.retriever.retrieve(alice, question).candidates == []
        answer = stack.pipeline.ask(alice, question)
        assert (answer.status, answer.citations) == ("insufficient_evidence", [])
        assert stack.generator.answers == []
        body = ask(client, question)
        assert (body["status"], body["answer"], body["citations"]) == (
            "insufficient_evidence",
            None,
            [],
        )

    # 1. Nothing indexed ever: the alias does not exist.
    assert qdrant_index.alias_target() is None
    assert_refused(ALICE_QUESTION)

    # 2. The alias points at a collection with no point.
    collection = qdrant_index.ensure_collection(
        rag_settings.index_model, rag_settings.embedding_dimension
    )
    assert collection == rag_settings.index_name
    qdrant_index.switch_alias(collection)
    assert list(qdrant_index.point_ids(collection)) == []
    assert_refused(ALICE_QUESTION)

    # 3. Points exist, but none Alice may see: an empty corpus for her.
    document_with_text(session_factory, bob, "theirs", BOB_TEXT, BASE)
    counters = index_everything(session_factory, qdrant_index, rag_settings)
    assert counters["embedded"] == 1
    assert_refused(BOB_QUESTION)
    # Positive control: the corpus is not empty for its owner.
    login_as_existing(client, "bob@example.org")
    assert ask(client, BOB_QUESTION)["status"] == "answered"


@pytest.mark.parametrize(
    ("rewrite", "rerank"), [(False, True), (True, False), (False, False)]
)
def test_disabling_rewrite_and_rerank_still_works(
    rewrite: bool,
    rerank: bool,
    clean_database: None,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    alice = add_user(session_factory, "alice@example.org")
    mine = document_with_text(session_factory, alice, "mine", ALICE_TEXT, BASE)
    document_with_text(session_factory, None, "public", PUBLIC_TEXT, BASE)
    index_everything(session_factory, qdrant_index, rag_settings)
    # Same model and dimension: the same index, only the two switches differ.
    switched = rag_settings.model_copy(
        update={"rewrite_enabled": rewrite, "rerank_enabled": rerank}
    )
    assert switched.index_name == rag_settings.index_name
    stack = rag_stack(session_factory, qdrant_index, switched)

    retrieved = stack.retriever.retrieve(alice, ALICE_QUESTION)
    assert len(retrieved.candidates) == 2
    # Without reranking there is no relevance score (RRF ranks are not one).
    assert (retrieved.best_score is None) is (not rerank)
    answer = stack.pipeline.ask(alice, ALICE_QUESTION)
    assert answer.status == "answered"
    assert {c.document_id for c in answer.citations} == {mine}
    assert answer.citations[0].quote == ALICE_TEXT
    # The generator is asked for rewrites only when rewriting is on.
    assert bool(stack.generator.rewrites) is rewrite

    # The refusal still holds. With reranking on it comes before generation;
    # without a score the generator decides (level 2) and declines.
    calls = len(stack.generator.answers)
    refused = stack.pipeline.ask(alice, UNANSWERABLE)
    assert refused.status == "insufficient_evidence"
    assert len(stack.generator.answers) - calls == (0 if rerank else 1)
