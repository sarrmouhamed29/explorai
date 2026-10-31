"""Types shared by all sources."""

from dataclasses import dataclass, field


class SourceError(Exception):
    """A source could not list its items (network failure, malformed feed)."""


@dataclass(frozen=True)
class SourceItem:
    external_id: str
    url: str
    title: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
