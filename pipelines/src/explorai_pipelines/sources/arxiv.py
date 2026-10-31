"""arXiv: metadata from the official Atom API, PDF links for the download step."""

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.netsafe import (
    DownloadTooLargeError,
    FetchResult,
    Resolver,
    UnsafeUrlError,
    UrlPolicy,
    default_resolver,
    fetch_bytes,
)
from explorai_pipelines.sources.base import SourceError, SourceItem

SOURCE_KIND = "arxiv"
API_URL = "https://export.arxiv.org/api/query"
ARXIV_POLICY = UrlPolicy(frozenset({"arxiv.org", "export.arxiv.org"}))
MAX_FEED_BYTES = 5_000_000
MAX_RETRY_AFTER_SECONDS = 120.0

_ATOM = "{http://www.w3.org/2005/Atom}"
_ABS_ID = re.compile(r"/abs/(?P<id>.+?)(?:v\d+)?$")


def _https(url: str) -> str:
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


def _text(element: ET.Element | None) -> str:
    return " ".join((element.text or "").split()) if element is not None else ""


class ArxivSource:
    def __init__(
        self,
        client: httpx.Client,
        settings: PipelineSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        resolve: Resolver = default_resolver,
    ) -> None:
        self._client = client
        self._min_interval = settings.arxiv_min_interval_seconds
        self._user_agent = f"explorai-ingestion (mailto:{settings.contact_email})"
        self._sleep = sleep
        self._clock = clock
        self._resolve = resolve
        self._last_request_end: float | None = None

    def _wait_turn(self) -> None:
        if self._last_request_end is None:
            return
        wait = self._min_interval - (self._clock() - self._last_request_end)
        if wait > 0:
            self._sleep(wait)

    def _get(self, url: str, max_bytes: int) -> FetchResult:
        """The single gateway to arxiv.org: spacing, User-Agent, Retry-After."""
        self._wait_turn()
        try:
            return fetch_bytes(
                self._client,
                url,
                ARXIV_POLICY,
                max_bytes,
                resolve=self._resolve,
                headers={"User-Agent": self._user_agent},
            )
        except httpx.HTTPStatusError as error:
            self._honour_retry_after(error.response)
            raise
        finally:
            self._last_request_end = self._clock()

    def _honour_retry_after(self, response: httpx.Response) -> None:
        """Wait as long as arXiv asked (seconds form, capped) before a retry."""
        if response.status_code not in (429, 503):
            return
        value = response.headers.get("retry-after", "")
        if value.isdigit():
            self._sleep(min(float(value), MAX_RETRY_AFTER_SECONDS))

    def fetch(self, item: SourceItem, max_bytes: int) -> FetchResult:
        """Download an item's PDF through the same politeness rules as the feed."""
        return self._get(item.url, max_bytes)

    def _download_feed(self, query: str, max_results: int) -> bytes:
        params = urlencode(
            {
                "search_query": query,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        try:
            result = self._get(f"{API_URL}?{params}", MAX_FEED_BYTES)
        except (httpx.HTTPError, UnsafeUrlError, DownloadTooLargeError) as error:
            raise SourceError(f"arXiv request failed: {error}") from error
        return result.content

    def list_items(self, query: str, max_results: int) -> list[SourceItem]:
        try:
            root = ET.fromstring(self._download_feed(query, max_results))
        except ET.ParseError as error:
            raise SourceError("arXiv returned a malformed feed") from error
        items: list[SourceItem] = []
        for entry in root.findall(f"{_ATOM}entry"):
            identifier = _text(entry.find(f"{_ATOM}id"))
            if "/api/errors" in identifier:
                raise SourceError(
                    f"arXiv error: {_text(entry.find(f'{_ATOM}summary'))}"
                )
            match = _ABS_ID.search(identifier)
            if match is None:
                raise SourceError(f"unexpected arXiv entry id: {identifier!r}")
            external_id = match.group("id")
            pdf_url = next(
                (
                    link.get("href", "")
                    for link in entry.findall(f"{_ATOM}link")
                    if link.get("title") == "pdf"
                ),
                f"https://arxiv.org/pdf/{external_id}",
            )
            authors = ", ".join(
                _text(name) for name in entry.findall(f"{_ATOM}author/{_ATOM}name")
            )
            items.append(
                SourceItem(
                    external_id=external_id,
                    url=_https(pdf_url),
                    title=_text(entry.find(f"{_ATOM}title")) or None,
                    metadata={
                        "authors": authors,
                        "published": _text(entry.find(f"{_ATOM}published")),
                        "summary": _text(entry.find(f"{_ATOM}summary")),
                    },
                )
            )
        return items
