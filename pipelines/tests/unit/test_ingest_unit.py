import pytest

from explorai_pipelines.ingest import Outcome, sniff_kind


def test_pdf_detected_from_magic_bytes_even_with_wrong_content_type() -> None:
    assert sniff_kind(b"%PDF-1.7 ...", "text/html", "https://x.org/a") == "pdf"


@pytest.mark.parametrize(
    ("content", "content_type", "url", "expected"),
    [
        (b"<html></html>", "text/html; charset=utf-8", "https://x.org/a", "html"),
        (b"<html></html>", None, "https://x.org/paper.pdf", "html"),
        (b"%PDF", None, "https://x.org/a", "pdf"),
    ],
)
def test_sniff_kind(
    content: bytes, content_type: str | None, url: str, expected: str
) -> None:
    assert sniff_kind(content, content_type, url) == expected


def test_outcome_values_are_the_documented_ones() -> None:
    assert {o.value for o in Outcome} == {
        "unchanged",
        "ingested",
        "new_version",
        "unextractable",
        "failed",
    }
