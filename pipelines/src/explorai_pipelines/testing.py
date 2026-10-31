"""Test support: build small PDFs in code and a worker that never finishes.

Fixtures are generated instead of committed so that each test shows exactly the
layout it relies on (columns, running headers, a missing text layer).
"""

import time
from collections.abc import Sequence
from multiprocessing.queues import Queue
from typing import Any

import pymupdf

# x0, y0, x1, y1, text
Box = tuple[float, float, float, float, str]
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0


def make_pdf(
    pages: Sequence[Sequence[Box]], *, fontsize: float = 11, title: str | None = None
) -> bytes:
    """One PDF page per entry; each box is a text box placed at fixed coordinates."""
    document = pymupdf.open()
    for boxes in pages:
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        for x0, y0, x1, y1, text in boxes:
            leftover = page.insert_textbox(
                pymupdf.Rect(x0, y0, x1, y1), text, fontsize=fontsize
            )
            if leftover < 0:
                raise ValueError(f"text does not fit its box: {text[:30]!r}")
    if title is not None:
        document.set_metadata({"title": title})
    data: bytes = document.tobytes()
    document.close()
    return data


def one_column_page(paragraphs: Sequence[str], top: float = 80.0) -> list[Box]:
    """Stack paragraphs in one full-width column, 90 points apart."""
    return [
        (60, top + 90 * i, 535, top + 90 * i + 80, text)
        for i, text in enumerate(paragraphs)
    ]


def two_column_page(left: str, right: str) -> list[Box]:
    return [(50, 100, 280, 700, left), (315, 100, 545, 700, right)]


def with_running_header(
    boxes: Sequence[Box], header: str, page_number: int
) -> list[Box]:
    return [
        (60, 30, 535, 50, header),
        *boxes,
        (280, 800, 320, 820, str(page_number)),
    ]


def image_only_pdf() -> bytes:
    """A PDF whose only content is a picture: no text layer, like a scan."""
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), False)
    pixmap.clear_with(200)
    page.insert_image(pymupdf.Rect(100, 100, 400, 400), pixmap=pixmap)
    data: bytes = document.tobytes()
    document.close()
    return data


def sleep_worker(data: bytes, queue: "Queue[Any]") -> None:
    """Stand-in extraction worker that never answers (timeout tests)."""
    time.sleep(60)
