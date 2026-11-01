from collections.abc import Callable, Iterator

import httpx
import pytest

from explorai_pipelines.netsafe import (
    DownloadTooLargeError,
    UnsafeUrlError,
    UrlPolicy,
    fetch_bytes,
)

POLICY = UrlPolicy(frozenset({"arxiv.org"}))
PUBLIC_IP = ["151.101.1.1"]
Handler = Callable[[httpx.Request], httpx.Response]


def resolver(mapping: dict[str, list[str]]) -> Callable[[str], list[str]]:
    return lambda host: mapping.get(host, PUBLIC_IP)


def client_for(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, content=b"pdf-bytes", headers={"content-type": "application/pdf"}
    )


def fetch(handler: Handler, url: str, mapping: dict[str, list[str]] | None = None):  # type: ignore[no-untyped-def]
    return fetch_bytes(
        client_for(handler), url, POLICY, 4096, resolve=resolver(mapping or {})
    )


def test_fetch_returns_content_and_type() -> None:
    result = fetch(ok, "https://arxiv.org/pdf/1")
    assert result.content == b"pdf-bytes"
    assert result.content_type == "application/pdf"
    assert result.final_url == "https://arxiv.org/pdf/1"


def test_domain_outside_allowlist_refused() -> None:
    with pytest.raises(UnsafeUrlError, match="allowlist"):
        fetch(ok, "https://evil.example/x")


def test_subdomain_of_allowed_domain_accepted() -> None:
    assert fetch(ok, "https://export.arxiv.org/x").content == b"pdf-bytes"


def test_lookalike_domain_refused() -> None:
    with pytest.raises(UnsafeUrlError):
        fetch(ok, "https://notarxiv.org/x")


def test_http_scheme_refused_by_default() -> None:
    with pytest.raises(UnsafeUrlError, match="scheme"):
        fetch(ok, "http://arxiv.org/x")


@pytest.mark.parametrize(
    "ip",
    ["10.0.0.5", "192.168.1.1", "127.0.0.1", "169.254.169.254", "::1", "172.16.0.9"],
)
def test_private_address_refused(ip: str) -> None:
    with pytest.raises(UnsafeUrlError, match="private"):
        fetch(ok, "https://arxiv.org/x", {"arxiv.org": [ip]})


def test_any_private_address_among_several_refuses() -> None:
    with pytest.raises(UnsafeUrlError, match="private"):
        fetch(ok, "https://arxiv.org/x", {"arxiv.org": ["151.101.1.1", "10.0.0.1"]})


def test_redirect_to_private_address_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "arxiv.org":
            return httpx.Response(302, headers={"location": "https://cdn.arxiv.org/x"})
        return ok(request)

    with pytest.raises(UnsafeUrlError, match="private"):
        fetch(handler, "https://arxiv.org/x", {"cdn.arxiv.org": ["10.1.1.1"]})


def test_redirect_to_other_domain_refused() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    with pytest.raises(UnsafeUrlError, match="allowlist"):
        fetch(handler, "https://arxiv.org/x")


def test_relative_redirect_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return ok(request)

    assert fetch(handler, "https://arxiv.org/old").final_url == "https://arxiv.org/new"


def test_redirect_loop_stops() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://arxiv.org/loop"})

    with pytest.raises(UnsafeUrlError, match="redirect"):
        fetch(handler, "https://arxiv.org/loop")


class CountingStream(httpx.SyncByteStream):
    chunk = b"x" * 1024

    def __init__(self) -> None:
        self.yielded = 0

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(10_000):
            self.yielded += len(self.chunk)
            yield self.chunk


def test_oversized_body_aborted_while_streaming() -> None:
    stream = CountingStream()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with pytest.raises(DownloadTooLargeError):
        fetch(handler, "https://arxiv.org/big")
    assert stream.yielded <= 4096 + 2 * 1024 * 64


def test_declared_content_length_too_large_refused_before_reading() -> None:
    stream = CountingStream()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, stream=stream, headers={"content-length": "999999999"}
        )

    with pytest.raises(DownloadTooLargeError):
        fetch(handler, "https://arxiv.org/big")
    assert stream.yielded == 0


def test_http_error_status_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        fetch(handler, "https://arxiv.org/x")
