"""Split sectioned text into overlapping word windows.

Words rather than tokens: no tokenizer dependency, and the window size is easy to
reason about. Overlap keeps a sentence cut at a window edge retrievable from the
next window. Windows never cross a section, so a chunk always has one heading.
"""

from collections.abc import Sequence
from dataclasses import dataclass

MIN_SECTION_WORDS = 5


@dataclass(frozen=True)
class SectionInput:
    heading: str | None
    text: str


@dataclass(frozen=True)
class ChunkText:
    ordinal: int
    heading: str | None
    text: str
    word_count: int


def embedding_text(heading: str | None, text: str) -> str:
    """Text fed to the dense and sparse encoders: heading, newline, body."""
    return f"{heading}\n{text}" if heading else text


def chunk_sections(
    sections: Sequence[SectionInput],
    *,
    target_words: int,
    overlap_words: int,
    max_chunks: int,
) -> list[ChunkText]:
    if target_words <= 0 or overlap_words < 0 or overlap_words >= target_words:
        raise ValueError(
            "overlap_words must be non-negative and smaller than target_words "
            "(a positive window)"
        )
    step = target_words - overlap_words
    chunks: list[ChunkText] = []
    for section in sections:
        words = section.text.split()
        if len(words) < MIN_SECTION_WORDS:
            continue
        start = 0
        while True:
            if len(chunks) >= max_chunks:
                return chunks
            window = words[start : start + target_words]
            chunks.append(
                ChunkText(len(chunks), section.heading, " ".join(window), len(window))
            )
            if start + target_words >= len(words):
                break
            start += step
    return chunks
