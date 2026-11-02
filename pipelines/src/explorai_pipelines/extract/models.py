"""Shared extraction result types."""

import re
from dataclasses import dataclass

MIN_TEXT_CHARS = 50

_NUMBERED_HEADING = re.compile(r"\d+(\.\d+)*\.?\s+[A-Z][^\n]{0,80}")
_NAMED_HEADING = re.compile(
    r"(abstract|introduction|related work|background|methods?|results|discussion|"
    r"conclusions?|acknowledg(e)?ments?|references|appendix)",
    re.IGNORECASE,
)


class Unextractable(Exception):
    """The content has no usable text (scanned, corrupt, empty): a verdict."""


class ExtractionAborted(Exception):
    """Extraction did not finish (timeout, worker killed): says nothing about the file.

    Unlike ``Unextractable`` this is not a verdict on the content, so no version is
    stored and the artifact is retried on the next run.
    """


@dataclass(frozen=True)
class ExtractedSection:
    heading: str | None
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    title: str | None
    sections: list[ExtractedSection]


def looks_like_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 90:
        return False
    if _NUMBERED_HEADING.fullmatch(line) and not line.endswith("."):
        return True
    if _NAMED_HEADING.fullmatch(line):
        return True
    letters = [ch for ch in line if ch.isalpha()]
    return len(letters) >= 4 and line.isupper() and len(line) <= 60


def split_sections(text: str) -> list[ExtractedSection]:
    """Split on heading-looking lines; text before the first heading has no heading."""
    sections: list[ExtractedSection] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(ExtractedSection(heading=heading, text=body))
        buffer.clear()

    for line in text.split("\n"):
        if looks_like_heading(line):
            flush()
            heading = line.strip()
        else:
            buffer.append(line)
    flush()
    return sections
