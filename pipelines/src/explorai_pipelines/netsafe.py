"""SSRF-safe, size-capped HTTP download.

Every hop, including redirects, is re-validated: scheme, domain allowlist and
the resolved addresses (a name that resolves to a private address is refused).
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

Resolver = Callable[[str], list[str]]
MAX_REDIRECTS = 5
CHUNK_SIZE = 64 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class UnsafeUrlError(Exception):
    """The URL (or a redirect target) is not allowed to be fetched."""


class DownloadTooLargeError(Exception):
    """The response exceeds the configured size cap."""


@dataclass(frozen=True)
class UrlPolicy:
    allowed_domains: frozenset[str]
    allow_http: bool = False


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    content_type: str | None
    final_url: str


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _domain_allowed(host: str, policy: UrlPolicy) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in policy.allowed_domains)


def _is_private(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not ip.is_global or ip.is_multicast


def check_url(
    url: str, policy: UrlPolicy, resolve: Resolver = default_resolver
) -> None:
    parts = urlsplit(url)
    allowed_schemes = {"https", "http"} if policy.allow_http else {"https"}
    if parts.scheme not in allowed_schemes:
        raise UnsafeUrlError(f"scheme not allowed: {parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if not host or not _domain_allowed(host, policy):
        raise UnsafeUrlError(f"host not in the domain allowlist: {host!r}")
    try:
        addresses = resolve(host)
    except OSError as error:
        raise UnsafeUrlError(f"cannot resolve host {host!r}") from error
    if not addresses or any(_is_private(address) for address in addresses):
        raise UnsafeUrlError(f"host resolves to a private address: {host!r}")


def fetch_bytes(
    client: httpx.Client,
    url: str,
    policy: UrlPolicy,
    max_bytes: int,
    *,
    resolve: Resolver = default_resolver,
) -> FetchResult:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        check_url(current, policy, resolve)
        with client.stream("GET", current, follow_redirects=False) as response:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise UnsafeUrlError("redirect without a Location header")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if (
                declared is not None
                and declared.isdigit()
                and int(declared) > max_bytes
            ):
                raise DownloadTooLargeError(f"declared size {declared} > {max_bytes}")
            body = bytearray()
            for chunk in response.iter_bytes(CHUNK_SIZE):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise DownloadTooLargeError(f"body exceeds {max_bytes} bytes")
            return FetchResult(
                content=bytes(body),
                content_type=response.headers.get("content-type"),
                final_url=current,
            )
    raise UnsafeUrlError(f"too many redirects (more than {MAX_REDIRECTS})")
