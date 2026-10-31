import multiprocessing
import time

import pytest

from explorai_pipelines.extract.models import ExtractionAborted, Unextractable
from explorai_pipelines.extract.pdf import (
    PDF_EXTRACTOR_VERSION,
    extract_pdf,
    extract_pdf_isolated,
)
from explorai_pipelines.testing import (
    die_worker,
    image_only_pdf,
    make_pdf,
    one_column_page,
    sleep_worker,
    two_column_page,
    with_running_header,
)

LOREM = (
    "This paragraph discusses retrieval augmented generation in some detail "
    "so that the page carries enough text to be extracted."
)


def test_extractor_version_is_pinned() -> None:
    assert PDF_EXTRACTOR_VERSION == "pdf-1"


def test_two_columns_read_left_then_right() -> None:
    left = "ALPHA first sentence of the left column. " + LOREM
    right = "OMEGA last sentence of the right column. " + LOREM
    data = make_pdf([two_column_page(left, right)])

    text = "\n".join(s.text for s in extract_pdf(data).sections)

    assert text.index("ALPHA") < text.index("OMEGA")
    assert text.index("OMEGA") > text.index("left column")


def test_running_header_and_page_numbers_removed() -> None:
    pages = [
        with_running_header(
            one_column_page([f"Body of page {i}. {LOREM}"]),
            f"Journal of Testing {10 + i}",
            i + 1,
        )
        for i in range(5)
    ]
    text = "\n".join(s.text for s in extract_pdf(make_pdf(pages)).sections)

    assert "Journal of Testing" not in text
    assert "Body of page 3" in text


def test_headings_split_into_sections_and_title_taken_from_first_line() -> None:
    page = one_column_page(
        [
            "A Study of Things",
            "Abstract",
            LOREM,
            "1 Introduction",
            LOREM,
            "2 Methods",
            LOREM,
        ],
        top=40,
    )
    document = extract_pdf(make_pdf([page[:5], page[5:]]))

    assert document.title == "A Study of Things"
    assert [s.heading for s in document.sections] == [
        None,
        "Abstract",
        "1 Introduction",
        "2 Methods",
    ]
    assert all(s.text for s in document.sections)


def test_metadata_title_wins_over_first_line() -> None:
    data = make_pdf([one_column_page([LOREM])], title="Real Title")
    assert extract_pdf(data).title == "Real Title"


def test_scanned_pdf_unextractable() -> None:
    with pytest.raises(Unextractable, match="text"):
        extract_pdf(image_only_pdf())


def test_corrupt_bytes_unextractable() -> None:
    with pytest.raises(Unextractable):
        extract_pdf(b"%PDF-1.4 this is not really a pdf \x00\x01")


def test_empty_bytes_unextractable() -> None:
    with pytest.raises(Unextractable):
        extract_pdf(b"")


def test_isolated_extraction_returns_document() -> None:
    data = make_pdf([one_column_page([LOREM])])
    document = extract_pdf_isolated(data, timeout_seconds=60)
    assert LOREM[:30] in document.sections[0].text


def test_isolated_extraction_propagates_unextractable() -> None:
    with pytest.raises(Unextractable, match="text"):
        extract_pdf_isolated(image_only_pdf(), timeout_seconds=60)


def test_isolated_timeout_kills_worker() -> None:
    started = time.monotonic()
    with pytest.raises(ExtractionAborted, match="timeout"):
        extract_pdf_isolated(b"x", timeout_seconds=1.0, target=sleep_worker)
    assert time.monotonic() - started < 5
    assert multiprocessing.active_children() == []


def test_worker_that_dies_is_an_aborted_extraction_not_a_verdict() -> None:
    with pytest.raises(ExtractionAborted, match="died"):
        extract_pdf_isolated(b"x", timeout_seconds=30, target=die_worker)
    assert multiprocessing.active_children() == []
