"""HTML extraction with trafilatura: main content only, as markdown sections."""

import trafilatura

from explorai_pipelines.extract.models import (
    MIN_TEXT_CHARS,
    ExtractedDocument,
    ExtractedSection,
    Unextractable,
)

HTML_EXTRACTOR_VERSION = "html-1"


def _split_markdown(markdown: str) -> list[ExtractedSection]:
    sections: list[ExtractedSection] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(ExtractedSection(heading=heading, text=body))
        buffer.clear()

    for line in markdown.split("\n"):
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip() or None
        else:
            buffer.append(line)
    flush()
    return sections


def extract_html(data: bytes, url: str | None = None) -> ExtractedDocument:
    markdown = trafilatura.extract(
        data,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if not markdown or len(markdown.strip()) < MIN_TEXT_CHARS:
        raise Unextractable("no extractable main content in the HTML")
    metadata = trafilatura.extract_metadata(data, default_url=url)
    title = metadata.title if metadata and metadata.title else None
    return ExtractedDocument(title=title, sections=_split_markdown(markdown))
