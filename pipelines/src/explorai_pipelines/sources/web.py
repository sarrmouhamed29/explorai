"""Generic web pages: a fixed list of URLs."""

from collections.abc import Sequence
from urllib.parse import urldefrag

from explorai_pipelines.sources.base import SourceItem

SOURCE_KIND = "web"


class WebSource:
    def __init__(self, urls: Sequence[str]) -> None:
        self._urls = urls

    def list_items(self) -> list[SourceItem]:
        seen: dict[str, SourceItem] = {}
        for raw in self._urls:
            url = urldefrag(raw).url
            seen.setdefault(url, SourceItem(external_id=url, url=url))
        return list(seen.values())
