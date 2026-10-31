import functools
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from prefect.testing.utilities import prefect_test_harness
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.models import DocumentVersion, IngestionRun
from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.flows import ingest_arxiv, ingest_web, reextract, runtime
from explorai_pipelines.repository import Repository
from explorai_pipelines.storage import LocalBlobStore
from explorai_pipelines.testing import make_pdf, one_column_page

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures"
LOREM = "Dense retrieval and sparse retrieval complement each other in practice. "
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(scope="module", autouse=True)
def prefect_harness() -> Iterator[None]:
    with prefect_test_harness():
        yield


@functools.cache
def pdf_for(label: str) -> bytes:
    return make_pdf([one_column_page([f"{label}. {LOREM * 2}"])])


def arxiv_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/query":
        return httpx.Response(200, content=(FIXTURES / "arxiv_feed.xml").read_bytes())
    label = request.url.path.rsplit("/", 1)[-1]
    return httpx.Response(
        200, content=pdf_for(label), headers={"content-type": "application/pdf"}
    )


@pytest.fixture
def install(
    monkeypatch: pytest.MonkeyPatch,
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
) -> Callable[[Handler], None]:
    fast = settings.model_copy(update={"arxiv_min_interval_seconds": 0.001})

    def _install(handler: Handler) -> None:
        built = runtime.Runtime(
            settings=fast,
            repo=repo,
            store=store,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            resolve=lambda _host: ["151.101.1.1"],
        )
        monkeypatch.setattr(runtime, "build_runtime", lambda: built)
        # No waiting between retries in tests.
        monkeypatch.setattr(
            ingest_arxiv,
            "fetch_item",
            ingest_arxiv.fetch_item.with_options(retry_delay_seconds=0),
        )
        monkeypatch.setattr(
            ingest_arxiv,
            "list_arxiv_items",
            ingest_arxiv.list_arxiv_items.with_options(retry_delay_seconds=0),
        )

    return _install


def test_arxiv_flow_ingests_and_reports(
    install: Callable[[Handler], None],
    session_factory: sessionmaker[Session],
) -> None:
    install(arxiv_handler)

    report = ingest_arxiv.ingest_arxiv_flow("cat:cs.CL", 5)

    assert report.counters["ingested"] == 2
    assert report.status == "completed"
    with session_factory() as session:
        assert len(session.scalars(select(DocumentVersion)).all()) == 2
        assert session.scalars(select(IngestionRun.status)).one() == "completed"


def test_flow_retries_transient_network_error(
    install: Callable[[Handler], None],
) -> None:
    calls: dict[str, int] = {}

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/query":
            calls[request.url.path] = calls.get(request.url.path, 0) + 1
            if calls[request.url.path] == 1:
                return httpx.Response(503)
        return arxiv_handler(request)

    install(flaky)

    report = ingest_arxiv.ingest_arxiv_flow("cat:cs.CL", 5)

    assert report.counters["ingested"] == 2
    assert report.counters["failed"] == 0
    assert all(n == 2 for n in calls.values())


def test_failed_item_does_not_fail_the_flow(
    install: Callable[[Handler], None],
    session_factory: sessionmaker[Session],
) -> None:
    def one_missing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("0112017"):
            return httpx.Response(404)
        return arxiv_handler(request)

    install(one_missing)

    report = ingest_arxiv.ingest_arxiv_flow("cat:cs.CL", 5)

    assert report.counters["ingested"] == 1
    assert report.counters["failed"] == 1
    assert report.status == "completed_with_errors"
    with session_factory() as session:
        status = session.scalars(select(IngestionRun.status)).one()
    assert status == "completed_with_errors"


def test_web_flow_ingests_pages(install: Callable[[Handler], None]) -> None:
    paragraph = LOREM * 3
    page = (
        "<html><head><title>Notes</title></head><body><article>"
        f"<h1>Notes</h1><p>{paragraph}</p><p>{paragraph}</p><p>{paragraph}</p>"
        "</article></body></html>"
    ).encode()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=page, headers={"content-type": "text/html"})

    install(handler)

    report = ingest_web.ingest_web_flow(["https://example.org/notes#top"])

    assert report.counters["ingested"] == 1


def test_reextract_flow_reports_counters(
    install: Callable[[Handler], None],
) -> None:
    install(arxiv_handler)
    ingest_arxiv.ingest_arxiv_flow("cat:cs.CL", 5)

    counters = reextract.reextract_flow("pdf-1")

    # Both PDFs already have a pdf-1 version: nothing to do.
    assert counters["new_version"] == 0
    assert counters["failed"] == 0


def test_reextract_flow_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="unknown extractor version"):
        reextract.reextract_flow("pdf-99")


def test_extra_flow_parameters_are_typed() -> None:
    # Parameters must stay JSON-friendly: they come from a deployment schedule.
    parameters: dict[str, Any] = ingest_arxiv.ingest_arxiv_flow.parameters.properties
    assert set(parameters) == {"query", "max_results"}
