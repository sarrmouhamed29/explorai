"""Validation of an uploaded file: size, type and title.

Everything here is pure (no I/O besides consuming the given chunks), so the
rules are unit-tested without a server.
"""

import unicodedata
from collections.abc import AsyncIterator

PDF_MAGIC = b"%PDF-"
TITLE_MAX_LENGTH = 200
UNTITLED = "Untitled document"


class TooLarge(Exception):
    """The body passed the size cap."""


class NotPdf(Exception):
    """The body does not start with the PDF signature."""


async def read_capped(chunks: AsyncIterator[bytes], max_bytes: int) -> bytes:
    """Collect ``chunks`` into one body, refusing as soon as it passes ``max_bytes``.

    The check runs after every chunk, so at most one chunk beyond the cap is
    ever held in memory, whatever the client declared in ``Content-Length``
    (or did not declare, with a chunked body).
    """
    body = bytearray()
    async for chunk in chunks:
        body += chunk
        if len(body) > max_bytes:
            raise TooLarge
    return bytes(body)


def require_pdf(data: bytes) -> None:
    """Decide the type by the bytes (magic number), never by name or header.

    ``Content-Type`` and the file extension are chosen by the client and prove
    nothing; every PDF starts with ``%PDF-`` (ISO 32000). This is a cheap
    first filter, not a guarantee that the file parses: the pipeline still
    marks a broken PDF as failed.
    """
    if not data.startswith(PDF_MAGIC):
        raise NotPdf


def _is_control(char: str) -> bool:
    # Cc: C0/C1 controls (NUL, ESC...). Cf: invisible format characters, such as
    # the right-to-left override that displays "report<RLO>fdp.exe" as
    # "reportexe.pdf".
    return unicodedata.category(char) in {"Cc", "Cf"}


def clean_title(filename: str | None) -> str:
    """A display title from a client file name, never used as a path.

    Basename only (both ``/`` and ``\\`` separators, whatever the client's OS),
    extension dropped, control characters removed, whitespace collapsed and at
    most 200 characters; ``"Untitled document"`` when nothing is left.
    """
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, _extension = name.rpartition(".")
    if dot:
        name = stem
    # Whitespace controls (tab, newline) become spaces through split(); the
    # other controls are dropped.
    name = "".join(c for c in name if c.isspace() or not _is_control(c))
    name = " ".join(name.split())[:TITLE_MAX_LENGTH].rstrip()
    return name or UNTITLED
