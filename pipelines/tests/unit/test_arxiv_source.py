from pathlib import Path

import httpx
import pytest

from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.sources.arxiv import ArxivSource
from explorai_pipelines.sources.base import SourceError

FIXTURES = Path(__file__).parent.parent / "fixtures"


def settings(interval: float = 3.0) -> PipelineSettings:
    return PipelineSettings(
        database_url="postgresql://u:p@127.0.0.1:5432/db",  # type: ignore[arg-type]
        blob_dir=Path("/tmp/blobs"),
        contact_email="me@example.org",
        arxiv_min_interval_seconds=interval,
    )


def source(
    handler: httpx.MockTransport | None = None,
    *,
    body: str = "arxiv_feed.xml",
    status: int = 200,
    sleeps: list[float] | None = None,
    clock: list[float] | None = None,
    requests: list[httpx.Request] | None = None,
) -> ArxivSource:
    payload = (FIXTURES / body).read_bytes()

    def respond(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, content=payload)

    client = httpx.Client(transport=handler or httpx.MockTransport(respond))
    ticks = iter(clock) if clock is not None else None
    return ArxivSource(
        client,
        settings(),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        clock=(lambda: next(ticks)) if ticks is not None else (lambda: 1000.0),
        resolve=lambda _host: ["151.101.1.1"],
    )


def test_parses_entries() -> None:
    items = source().list_items("cat:cs.CL", 5)

    assert [i.external_id for i in items] == ["2401.01234", "cs/0112017"]
    # The feed links over http; the safe fetch only accepts https.
    assert items[0].url == "https://arxiv.org/pdf/2401.01234v2"
    assert items[0].title == "Retrieval Augmented Generation Revisited"
    assert items[0].metadata["authors"] == "Ada Lovelace, Alan Turing"
    assert items[0].metadata["published"] == "2024-01-02T10:00:00Z"
    assert items[0].metadata["summary"] == "A study of RAG."


def test_entry_without_pdf_link_gets_a_constructed_pdf_url() -> None:
    items = source().list_items("cat:cs.CL", 5)
    assert items[1].url == "https://arxiv.org/pdf/cs/0112017"


def test_user_agent_contains_contact_and_query_is_sent() -> None:
    requests: list[httpx.Request] = []
    source(requests=requests).list_items("cat:cs.CL", 7)

    assert (
        requests[0].headers["user-agent"]
        == "explorai-ingestion (mailto:me@example.org)"
    )
    assert requests[0].url.params["search_query"] == "cat:cs.CL"
    assert requests[0].url.params["max_results"] == "7"


def test_requests_are_spaced() -> None:
    sleeps: list[float] = []
    # monotonic readings: the first request ends at 100; the second starts at 101.
    arxiv = source(sleeps=sleeps, clock=[100.0, 101.0, 101.0])
    arxiv.list_items("a", 1)
    arxiv.list_items("b", 1)

    assert sleeps == [pytest.approx(2.0)]


def test_empty_feed_returns_empty_list() -> None:
    assert source(body="arxiv_empty.xml").list_items("x", 5) == []


def test_malformed_feed_raises_source_error() -> None:
    with pytest.raises(SourceError, match="feed"):
        source(body="arxiv_malformed.xml").list_items("x", 5)


def test_arxiv_error_entry_raises_source_error() -> None:
    with pytest.raises(SourceError, match="incorrect id format"):
        source(body="arxiv_error.xml").list_items("x", 5)


def test_http_error_raises_source_error() -> None:
    with pytest.raises(SourceError, match="503"):
        source(status=503).list_items("x", 5)
