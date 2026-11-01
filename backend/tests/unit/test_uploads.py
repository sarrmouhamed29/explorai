"""Upload validation: streamed size cap, magic bytes, title cleaning."""

from collections.abc import AsyncIterator

import pytest

from explorai_backend.uploads import (
    NotPdf,
    TooLarge,
    clean_title,
    read_capped,
    require_pdf,
)


class CountingChunks:
    """An async chunk source that records how many chunks were consumed."""

    def __init__(self, chunk: bytes, count: int) -> None:
        self.chunk = chunk
        self.count = count
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.count):
            self.consumed += 1
            yield self.chunk


async def test_read_capped_stops_at_the_cap() -> None:
    source = CountingChunks(b"x" * 10, count=100)

    with pytest.raises(TooLarge):
        await read_capped(source.__aiter__(), max_bytes=35)

    # 4 chunks (40 bytes) pass the 35-byte cap; the other 96 are never read.
    assert source.consumed == 4


async def test_read_capped_returns_a_body_at_the_cap() -> None:
    source = CountingChunks(b"x" * 10, count=3)

    assert await read_capped(source.__aiter__(), max_bytes=30) == b"x" * 30


async def test_read_capped_skips_empty_chunks() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"ab"
        yield b""
        yield b"cd"

    assert await read_capped(chunks(), max_bytes=4) == b"abcd"


def test_empty_body_is_not_pdf() -> None:
    with pytest.raises(NotPdf):
        require_pdf(b"")


def test_html_body_is_not_pdf() -> None:
    with pytest.raises(NotPdf):
        require_pdf(b"<!doctype html><html>%PDF-1.7</html>")


def test_truncated_magic_is_not_pdf() -> None:
    with pytest.raises(NotPdf):
        require_pdf(b"%PDF")


def test_pdf_magic_is_accepted() -> None:
    require_pdf(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\x\\report.pdf", "report"),
        ("paper.final.pdf", "paper.final"),
        ("  many   spaces\there .pdf", "many spaces here"),
        (".pdf", "Untitled document"),
        ("", "Untitled document"),
        (None, "Untitled document"),
        ("dir/", "Untitled document"),
    ],
)
def test_clean_title_strips_paths_and_controls(
    filename: str | None, expected: str
) -> None:
    assert clean_title(filename) == expected


def test_clean_title_removes_control_characters() -> None:
    # Built with chr(): NUL, ESC, a bidi override and a newline.
    name = "a" + chr(0) + "b" + chr(27) + "[31mc" + chr(0x202E) + "d" + chr(10) + "e"

    assert clean_title(name + ".pdf") == "ab[31mcd e"


def test_clean_title_keeps_non_ascii_text() -> None:
    assert clean_title("r" + chr(0xE9) + "sum" + chr(0xE9) + ".pdf") == (
        "r" + chr(0xE9) + "sum" + chr(0xE9)
    )


def test_clean_title_is_bounded() -> None:
    assert clean_title("x" * 500 + ".pdf") == "x" * 200
    # Cut at 200, then trailing whitespace from the cut is stripped.
    assert clean_title("y" * 199 + " z") == "y" * 199
