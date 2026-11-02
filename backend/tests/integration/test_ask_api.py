"""POST /api/v1/ask against PostgreSQL (RLS), Redis db 15 and a real Qdrant.

The models are the deterministic fakes of ``explorai_ai`` and documents are
indexed by the real indexing function of ``explorai_pipelines``.
"""

import uuid
from datetime import UTC, datetime

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.config import RagSettings
from explorai_ai.index import QdrantIndex
from explorai_db.models import DocumentVersion
from integration import helpers
from integration.helpers import (
    index_everything,
    login_as,
    login_as_existing,
)

pytestmark = pytest.mark.integration

ASK = "/api/v1/ask"
BASE = datetime(2026, 1, 1, tzinfo=UTC)
ALICE_TEXT = (
    "Photonic lattices guide entangled photons through silicon waveguides "
    "at room temperature."
)
PUBLIC_TEXT = (
    "Glacier meltwater carries fine sediment that colours alpine lakes "
    "turquoise every summer."
)
BOB_TEXT = (
    "Bob ferments purple carrots with juniper berries for twelve weeks in "
    "a cold cellar."
)
ALICE_QUESTION = "How do photonic lattices guide entangled photons?"
PUBLIC_QUESTION = "Why does glacier meltwater colour alpine lakes turquoise?"
BOB_QUESTION = "How long does Bob ferment purple carrots with juniper berries?"


def document_with_text(
    factory: sessionmaker[Session], owner: uuid.UUID | None, title: str, text: str
) -> uuid.UUID:
    return helpers.document_with_text(factory, owner, title, text, BASE)


def reserved(redis_client: redis.Redis, user: uuid.UUID) -> int:
    value = redis_client.get(f"ask:user:{user}")
    return 0 if value is None else int(value)


def test_document_id_not_visible_is_404_identical_to_unknown(
    rag_app: FastAPI,
    client: TestClient,
    session_factory: sessionmaker[Session],
    redis_client: redis.Redis,
) -> None:
    alice = login_as(client, "alice@example.org")
    bob = login_as(client, "bob@example.org")
    private = document_with_text(session_factory, bob, "bob", BOB_TEXT)
    login_as_existing(client, "alice@example.org")

    foreign = client.post(
        ASK, json={"question": BOB_QUESTION, "document_id": str(private)}
    )
    unknown = client.post(
        ASK, json={"question": BOB_QUESTION, "document_id": str(uuid.uuid4())}
    )

    assert foreign.status_code == unknown.status_code == 404
    # Same bytes: the answer cannot tell "exists but not yours" from "missing".
    assert foreign.content == unknown.content
    # Neither 404 used up an hourly reservation.
    assert reserved(redis_client, alice) == 0


def test_ask_over_own_and_public_documents(
    rag_app: FastAPI,
    client: TestClient,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    alice = login_as(client, "alice@example.org")
    bob = login_as(client, "bob@example.org")
    login_as_existing(client, "alice@example.org")
    mine = document_with_text(session_factory, alice, "mine", ALICE_TEXT)
    public = document_with_text(session_factory, None, "public", PUBLIC_TEXT)
    theirs = document_with_text(session_factory, bob, "theirs", BOB_TEXT)
    counters = index_everything(session_factory, qdrant_index, rag_settings)
    assert counters["embedded"] == 3

    def ask(question: str, **extra: object) -> dict[str, object]:
        response = client.post(ASK, json={"question": question, **extra})
        assert response.status_code == 200, response.text
        assert "juniper" not in response.text.lower()
        body: dict[str, object] = response.json()
        return body

    def cited(body: dict[str, object]) -> set[str]:
        citations = body["citations"]
        assert isinstance(citations, list)
        return {citation["document_id"] for citation in citations}

    own = ask(ALICE_QUESTION)
    assert own["status"] == "answered"
    assert cited(own) == {str(mine)}
    citation = own["citations"][0]  # type: ignore[index]
    assert citation["quote"] in ALICE_TEXT
    assert citation["document_title"] == "mine"

    shared = ask(PUBLIC_QUESTION)
    assert shared["status"] == "answered"
    assert cited(shared) == {str(public)}

    # Someone else's private text is never an answer.
    foreign = ask(BOB_QUESTION)
    assert foreign["status"] == "insufficient_evidence"
    assert str(theirs) not in cited(foreign)

    # Scope and document_id narrow the search.
    assert ask(ALICE_QUESTION, scope="public")["status"] == "insufficient_evidence"
    assert ask(PUBLIC_QUESTION, scope="mine")["status"] == "insufficient_evidence"
    narrowed = ask(ALICE_QUESTION, document_id=str(public))
    assert narrowed["status"] == "insufficient_evidence"
    public_by_id = ask(PUBLIC_QUESTION, document_id=str(public))
    assert cited(public_by_id) == {str(public)}


def test_empty_index_is_a_refusal_not_an_error(
    rag_app: FastAPI, client: TestClient
) -> None:
    # Nothing indexed yet: the collection of the test settings does not exist.
    login_as(client, "alice@example.org")

    response = client.post(ASK, json={"question": ALICE_QUESTION})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "insufficient_evidence"


def test_asking_about_a_duplicate_document_answers_from_its_canonical_version(
    rag_app: FastAPI,
    client: TestClient,
    session_factory: sessionmaker[Session],
    qdrant_index: QdrantIndex,
    rag_settings: RagSettings,
) -> None:
    alice = login_as(client, "alice@example.org")
    login_as(client, "bob@example.org")
    login_as_existing(client, "alice@example.org")
    canonical_doc = document_with_text(session_factory, alice, "mine", ALICE_TEXT)
    duplicate_doc = helpers.add_document(
        session_factory, alice, title="copy", created_at=BASE
    )
    with session_factory.begin() as session:
        canonical = session.scalars(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == canonical_doc
            )
        ).one()
    duplicate = helpers.add_version(
        session_factory, duplicate_doc, "ok", BASE, texts=(ALICE_TEXT,)
    )
    with session_factory.begin() as session:
        session.get_one(DocumentVersion, duplicate).duplicate_of = canonical
    index_everything(session_factory, qdrant_index, rag_settings)

    # The duplicate reads as indexed (its canonical version is)...
    shown = client.get(f"/api/v1/documents/{duplicate_doc}")
    assert shown.json()["indexed"] is True
    # ...and narrowing a question to it answers from that canonical version.
    response = client.post(
        ASK, json={"question": ALICE_QUESTION, "document_id": str(duplicate_doc)}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert {c["document_id"] for c in body["citations"]} == {str(canonical_doc)}

    # Bob cannot use Alice's duplicate id: the same 404 as an unknown id.
    login_as_existing(client, "bob@example.org")
    foreign = client.post(
        ASK, json={"question": ALICE_QUESTION, "document_id": str(duplicate_doc)}
    )
    unknown = client.post(
        ASK, json={"question": ALICE_QUESTION, "document_id": str(uuid.uuid4())}
    )
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.content == unknown.content
