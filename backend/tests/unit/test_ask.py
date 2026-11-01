"""POST /api/v1/ask without services: fake pipeline, fakeredis, overridden user.

The route's job is mapping: request validation, the hourly reservation, the
visibility check of ``document_id`` and the pipeline's outcomes to HTTP codes.
Retrieval and generation themselves are tested in ``ai/``.
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import fakeredis
import httpx2
import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from explorai_ai.citations import Citation
from explorai_ai.config import RagSettings
from explorai_ai.errors import (
    GenerationFailed,
    ProviderUnavailable,
    RetrievalUnavailable,
)
from explorai_ai.fakes import FakeGenerator
from explorai_ai.models import AnswerResult, Usage
from explorai_ai.pipeline import AnswerPipeline
from explorai_ai.retriever import Candidate, RetrievalResult
from explorai_ai.scope import Scope
from explorai_backend import deps
from explorai_backend.api import ask as ask_api
from explorai_backend.api.auth import current_user
from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.config import get_settings
from explorai_backend.main import create_app
from explorai_backend.repositories.documents import DocumentRow
from explorai_backend.services.ask import AskService, TooManyQuestions
from explorai_backend.services.auth import PublicUser
from explorai_backend.services.documents import DocumentNotFound

ASK = "/api/v1/ask"
USER = PublicUser(id=uuid.uuid4(), email="a@example.org")
LIMIT = 2
DOCUMENT = uuid.uuid4()
CHUNK = uuid.uuid4()
REFUSAL = AnswerResult("insufficient_evidence", None, [], Usage(3, 0))
ANSWER = AnswerResult(
    "answered",
    "According to [1]: attention is all you need.",
    [
        Citation(
            source=1,
            quote="attention is all you need",
            chunk_id=CHUNK,
            document_id=DOCUMENT,
            document_title="Transformers",
            heading="Abstract",
        )
    ],
    Usage(12, 7),
)


class FakePipeline:
    """Returns a scripted result, or raises a scripted error; records the calls."""

    def __init__(
        self, result: AnswerResult = REFUSAL, error: BaseException | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[uuid.UUID, str, Scope, uuid.UUID | None]] = []

    def ask(
        self,
        user_id: uuid.UUID,
        question: str,
        scope: Scope = "all",
        document_id: uuid.UUID | None = None,
    ) -> AnswerResult:
        self.calls.append((user_id, question, scope, document_id))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/15")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    get_settings.cache_clear()
    deps.get_rag_settings.cache_clear()
    yield
    deps.get_rag_settings.cache_clear()


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def make_app(
    pipeline: object, redis_client: redis.Redis, *, logged_in: bool = True
) -> FastAPI:
    app = create_app()
    if logged_in:
        app.dependency_overrides[current_user] = lambda: USER
    app.dependency_overrides[deps.get_answer_pipeline] = lambda: pipeline
    app.dependency_overrides[deps.get_redis] = lambda: redis_client
    rag = RagSettings(provider="fake", ask_max_per_hour=LIMIT)
    app.dependency_overrides[deps.get_rag_settings] = lambda: rag
    return app


def reserved(redis_client: redis.Redis) -> int:
    value = redis_client.get(f"ask:user:{USER.id}")
    return 0 if value is None else int(value)


def post(app: FastAPI, body: object) -> httpx2.Response:
    return TestClient(app, raise_server_exceptions=False).post(ASK, json=body)


def limiter_on(redis_client: redis.Redis) -> FixedWindowLimiter:
    return FixedWindowLimiter(redis_client, "ask:user", LIMIT, 3600)


@pytest.mark.parametrize("question", ["", " ", "\t\n  ", chr(0x3000)])
def test_empty_or_whitespace_question_is_422(
    redis_client: fakeredis.FakeRedis, question: str
) -> None:
    pipeline = FakePipeline()
    response = post(make_app(pipeline, redis_client), {"question": question})

    assert response.status_code == 422
    assert pipeline.calls == []
    # Validation comes before the reservation: a bad request costs nothing.
    assert reserved(redis_client) == 0


def test_too_long_question_is_422(redis_client: fakeredis.FakeRedis) -> None:
    pipeline = FakePipeline()
    app = make_app(pipeline, redis_client)

    assert post(app, {"question": "x" * 2001}).status_code == 422
    assert post(app, {"question": "x" * 2000}).status_code == 200
    assert len(pipeline.calls) == 1


def test_rejected_question_is_not_echoed(redis_client: fakeredis.FakeRedis) -> None:
    secret = "distinctive-" + "y" * 2000
    response = post(make_app(FakePipeline(), redis_client), {"question": secret})

    assert response.status_code == 422
    assert "distinctive-" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"question": "q?", "scope": "everything"},
        {"question": "q?", "scope": None},
        {"question": "q?", "document_id": "not-a-uuid"},
        {"scope": "all"},
    ],
)
def test_invalid_scope_is_422(
    redis_client: fakeredis.FakeRedis, body: dict[str, object]
) -> None:
    pipeline = FakePipeline()

    assert post(make_app(pipeline, redis_client), body).status_code == 422
    assert pipeline.calls == []


def test_request_is_passed_to_the_pipeline(redis_client: fakeredis.FakeRedis) -> None:
    pipeline = FakePipeline()
    app = make_app(pipeline, redis_client)
    service = AskService(pipeline, Documents({DOCUMENT}), limiter_on(redis_client))
    app.dependency_overrides[ask_api.get_ask_service] = lambda: service

    response = post(
        app, {"question": "What?", "scope": "mine", "document_id": str(DOCUMENT)}
    )

    assert response.status_code == 200, response.text
    assert pipeline.calls == [(USER.id, "What?", "mine", DOCUMENT)]


def test_invisible_document_id_is_404(redis_client: fakeredis.FakeRedis) -> None:
    pipeline = FakePipeline()
    app = make_app(pipeline, redis_client)
    service = AskService(pipeline, Documents(set()), limiter_on(redis_client))
    app.dependency_overrides[ask_api.get_ask_service] = lambda: service

    response = post(app, {"question": "What?", "document_id": str(uuid.uuid4())})

    assert response.status_code == 404
    assert response.json() == {"detail": "document not found"}
    assert pipeline.calls == []
    assert reserved(redis_client) == 0


def test_default_scope_is_all(redis_client: fakeredis.FakeRedis) -> None:
    pipeline = FakePipeline()

    assert post(make_app(pipeline, redis_client), {"question": "Why?"}).is_success
    assert pipeline.calls == [(USER.id, "Why?", "all", None)]


@pytest.mark.parametrize(
    "error",
    [
        RetrievalUnavailable("qdrant down"),
        ProviderUnavailable("voyage down"),
        GenerationFailed("bad reply"),
    ],
)
def test_retrieval_unavailable_is_503_and_gives_the_reservation_back(
    redis_client: fakeredis.FakeRedis, error: Exception
) -> None:
    app = make_app(FakePipeline(error=error), redis_client)

    response = post(app, {"question": "Is it up?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "answering is unavailable right now"}
    assert reserved(redis_client) == 0


def test_database_failure_is_503_and_gives_the_reservation_back(
    redis_client: fakeredis.FakeRedis,
) -> None:
    error = OperationalError("SELECT 1", {}, Exception("connection refused"))
    app = make_app(FakePipeline(error=error), redis_client)

    response = post(app, {"question": "Is the database up?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert reserved(redis_client) == 0


def test_unexpected_error_is_500_and_gives_the_reservation_back(
    redis_client: fakeredis.FakeRedis,
) -> None:
    app = make_app(FakePipeline(error=RuntimeError("bug")), redis_client)

    assert post(app, {"question": "Bug?"}).status_code == 500
    assert reserved(redis_client) == 0


def test_hourly_limit_is_429(redis_client: fakeredis.FakeRedis) -> None:
    pipeline = FakePipeline()
    app = make_app(pipeline, redis_client)

    statuses = [post(app, {"question": "Again?"}).status_code for _ in range(LIMIT + 2)]

    assert statuses == [200] * LIMIT + [429, 429]
    assert len(pipeline.calls) == LIMIT
    assert post(app, {"question": "Again?"}).json() == {"detail": "too many questions"}


def test_limit_is_per_user(redis_client: fakeredis.FakeRedis) -> None:
    pipeline = FakePipeline()
    app = make_app(pipeline, redis_client)
    for _ in range(LIMIT):
        post(app, {"question": "Mine?"})
    other = PublicUser(id=uuid.uuid4(), email="b@example.org")
    app.dependency_overrides[current_user] = lambda: other

    assert post(app, {"question": "Mine?"}).status_code == 200


def test_redis_down_is_503(redis_client: fakeredis.FakeRedis) -> None:
    server = fakeredis.FakeServer()
    server.connected = False
    pipeline = FakePipeline()
    app = make_app(pipeline, fakeredis.FakeRedis(server=server))

    response = post(app, {"question": "Is Redis up?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "rate limiter unavailable"}
    assert pipeline.calls == []


def test_refusal_is_200_with_status_insufficient_evidence(
    redis_client: fakeredis.FakeRedis,
) -> None:
    response = post(make_app(FakePipeline(REFUSAL), redis_client), {"question": "?!"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "insufficient_evidence",
        "answer": None,
        "citations": [],
        "usage": {"input_tokens": 3, "output_tokens": 0},
    }
    # A refusal is a served answer: it keeps its reservation.
    assert reserved(redis_client) == 1


def test_answer_is_200_with_its_citations(redis_client: fakeredis.FakeRedis) -> None:
    response = post(make_app(FakePipeline(ANSWER), redis_client), {"question": "Q?"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "answered",
        "answer": "According to [1]: attention is all you need.",
        "citations": [
            {
                "source": 1,
                "quote": "attention is all you need",
                "chunk_id": str(CHUNK),
                "document_id": str(DOCUMENT),
                "document_title": "Transformers",
                "section": "Abstract",
            }
        ],
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }
    assert reserved(redis_client) == 1


class ScriptedRetriever:
    def __init__(self, text: str) -> None:
        self._text = text

    def retrieve(
        self,
        user_id: uuid.UUID,
        question: str,
        scope: Scope = "all",
        document_id: uuid.UUID | None = None,
    ) -> RetrievalResult:
        candidate = Candidate(
            chunk_id=CHUNK,
            document_id=DOCUMENT,
            version_id=uuid.uuid4(),
            document_title="Zebra paper",
            heading="Intro",
            text=self._text,
            score=0.9,
        )
        return RetrievalResult([candidate], 0.9)


def test_no_question_text_in_logs(
    redis_client: fakeredis.FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    # The real pipeline (with a scripted retriever and the fake generator), so its
    # own log lines are covered too. The answer quotes the source text.
    question = "Which quagga zebroid crossbreeds exist?"
    source = "Quagga zebroid crossbreeds exist in captivity. More text follows."
    pipeline = AnswerPipeline(
        ScriptedRetriever(source), FakeGenerator(), RagSettings(provider="fake")
    )
    caplog.set_level(logging.DEBUG)

    answered = post(make_app(pipeline, redis_client), {"question": question})
    failing = make_app(FakePipeline(error=RetrievalUnavailable("x")), redis_client)
    unavailable = post(failing, {"question": question})

    assert answered.status_code == 200
    assert answered.json()["status"] == "answered"
    assert unavailable.status_code == 503
    assert caplog.records  # the pipeline did log its counters
    for record in caplog.records:
        logged = record.getMessage().lower()
        for word in ("quagga", "zebroid", "crossbreeds", "captivity"):
            assert word not in logged, record.getMessage()


def test_login_required(redis_client: fakeredis.FakeRedis) -> None:
    pipeline = FakePipeline()
    app = make_app(pipeline, redis_client, logged_in=False)

    response = post(app, {"question": "Who am I?"})

    assert response.status_code == 401
    assert pipeline.calls == []
    assert reserved(redis_client) == 0


# AskService directly: the visibility check and the reservation's lifecycle.


class Documents:
    def __init__(self, visible: set[uuid.UUID]) -> None:
        self._visible = visible

    def get(self, user_id: uuid.UUID, document_id: uuid.UUID) -> DocumentRow | None:
        if document_id not in self._visible:
            return None
        return DocumentRow(
            document_id, "t", "arxiv", False, "ready", datetime(2026, 1, 1, tzinfo=UTC)
        )


def test_invisible_document_is_not_found_and_gives_the_reservation_back(
    redis_client: fakeredis.FakeRedis,
) -> None:
    pipeline = FakePipeline()
    service = AskService(pipeline, Documents(set()), limiter_on(redis_client))

    with pytest.raises(DocumentNotFound):
        service.ask(USER.id, "Q?", "all", uuid.uuid4())

    assert pipeline.calls == []
    assert reserved(redis_client) == 0


def test_visible_document_reaches_the_pipeline(
    redis_client: fakeredis.FakeRedis,
) -> None:
    pipeline = FakePipeline(ANSWER)
    service = AskService(pipeline, Documents({DOCUMENT}), limiter_on(redis_client))

    assert service.ask(USER.id, "Q?", "public", DOCUMENT) is ANSWER
    assert pipeline.calls == [(USER.id, "Q?", "public", DOCUMENT)]
    assert reserved(redis_client) == 1


def test_over_the_limit_never_reaches_the_pipeline(
    redis_client: fakeredis.FakeRedis,
) -> None:
    pipeline = FakePipeline()
    service = AskService(pipeline, Documents(set()), limiter_on(redis_client))
    for _ in range(LIMIT):
        service.ask(USER.id, "Q?")

    with pytest.raises(TooManyQuestions):
        service.ask(USER.id, "Q?")

    assert len(pipeline.calls) == LIMIT
    # The refused attempt gave its reservation back: the count stays at the limit.
    assert reserved(redis_client) == LIMIT
