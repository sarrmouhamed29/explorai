"""PDF extraction with PyMuPDF: reading order, cleaning, sections.

``extract_pdf`` is a pure function. ``extract_pdf_isolated`` runs it in a
separate spawned process with a timeout, so a hostile or pathological PDF that
hangs or crashes the parser cannot take the pipeline down.
"""

import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.queues import Queue
from queue import Empty
from typing import Any

import pymupdf

from explorai_pipelines.clean import join_hyphenated, normalize, remove_repeated_lines
from explorai_pipelines.extract.models import (
    MIN_TEXT_CHARS,
    ExtractedDocument,
    Unextractable,
    split_sections,
)

PDF_EXTRACTOR_VERSION = "pdf-1"
MAX_PAGES = 1000
# Fraction of the page width around the middle that decides "column or full width".
_COLUMN_TOLERANCE = 0.05

Worker = Callable[[bytes, "Queue[Any]"], None]


def _page_text(page: pymupdf.Page) -> str:
    """Text of one page in reading order, left column before right column."""
    middle = page.rect.width / 2
    tolerance = page.rect.width * _COLUMN_TOLERANCE
    blocks = sorted(
        (b for b in page.get_text("blocks") if b[6] == 0),
        key=lambda b: (b[1], b[0]),
    )
    ordered: list[str] = []
    left: list[str] = []
    right: list[str] = []

    def flush_columns() -> None:
        ordered.extend(left)
        ordered.extend(right)
        left.clear()
        right.clear()

    for x0, _y0, x1, _y1, text, *_ in blocks:
        if x0 < middle - tolerance and x1 > middle + tolerance:
            flush_columns()
            ordered.append(text)
        elif x1 <= middle + tolerance:
            left.append(text)
        else:
            right.append(text)
    flush_columns()
    return normalize("\n".join(ordered))


def extract_pdf(data: bytes) -> ExtractedDocument:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as error:
        raise Unextractable("not a readable PDF") from error
    with document:
        if document.needs_pass:
            raise Unextractable("PDF is password protected")
        if document.page_count > MAX_PAGES:
            raise Unextractable(f"PDF has more than {MAX_PAGES} pages")
        pages = [_page_text(document[i]) for i in range(document.page_count)]
        metadata_title = (document.metadata or {}).get("title") or None
    text = normalize(join_hyphenated("\n".join(remove_repeated_lines(pages))))
    if len(text) < MIN_TEXT_CHARS:
        raise Unextractable("no extractable text layer (scanned or empty PDF)")
    sections = split_sections(text)
    first_line = text.split("\n", 1)[0].strip()
    title = metadata_title.strip() if metadata_title else None
    return ExtractedDocument(title=title or first_line[:200] or None, sections=sections)


def _worker(data: bytes, queue: "Queue[Any]") -> None:
    try:
        queue.put(("ok", extract_pdf(data)))
    except Unextractable as error:
        queue.put(("unextractable", str(error)))
    except Exception as error:  # noqa: BLE001 - report any parser failure to the parent
        queue.put(("unextractable", f"extraction failed: {type(error).__name__}"))


def extract_pdf_isolated(
    data: bytes, timeout_seconds: float, *, target: Worker = _worker
) -> ExtractedDocument:
    """Run ``extract_pdf`` in a spawned process; kill it after the timeout.

    ``target`` is only replaced by tests (a worker that never answers).
    """
    context = multiprocessing.get_context("spawn")
    queue: Queue[Any] = context.Queue(maxsize=1)
    process = context.Process(target=target, args=(data, queue), daemon=True)
    process.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Unextractable("extraction timeout")
            try:
                kind, payload = queue.get(timeout=min(0.2, remaining))
                break
            except Empty:
                if not process.is_alive():
                    try:
                        kind, payload = queue.get(timeout=0.5)
                        break
                    except Empty:
                        raise Unextractable("extraction worker died") from None
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
        queue.close()
    if kind == "ok":
        result: ExtractedDocument = payload
        return result
    raise Unextractable(str(payload))
