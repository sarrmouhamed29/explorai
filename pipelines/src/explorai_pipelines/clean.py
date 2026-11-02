"""Pure text-cleaning functions applied to extracted text."""

import re
import unicodedata

_CONTROL = re.compile(r"[^\S\n]+")
_BLANK_RUNS = re.compile(r"\n{3,}")
# A hyphen at a line end followed by a lowercase letter is a split word; a
# capital or a digit after it means a real hyphen (names, ranges). Known limit:
# a genuinely hyphenated compound split across lines ("well-\nknown") is joined.
_SPLIT_WORD = re.compile(r"(?<=\w)-\n\s*(?=[a-z])")
_DIGITS = re.compile(r"\d+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )
    lines = [_CONTROL.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def join_hyphenated(text: str) -> str:
    return _SPLIT_WORD.sub("", text)


def _line_key(line: str) -> str:
    return _DIGITS.sub("#", " ".join(line.lower().split()))


def remove_repeated_lines(pages: list[str], min_page_ratio: float = 0.5) -> list[str]:
    """Drop running headers, footers and page numbers.

    A first or last line (digits ignored) that repeats on at least
    ``min_page_ratio`` of the pages is removed from those pages. Fewer than
    three pages give too little evidence, so they are returned untouched.
    """
    if len(pages) < 3:
        return pages
    split = [page.split("\n") for page in pages]
    counts: dict[str, int] = {}
    for lines in split:
        edges = (
            {lines[0], lines[-1]} if lines[0].strip() or lines[-1].strip() else set()
        )
        for key in {_line_key(line) for line in edges if line.strip()}:
            counts[key] = counts.get(key, 0) + 1
    repeated = {k for k, n in counts.items() if n / len(pages) >= min_page_ratio}
    cleaned: list[str] = []
    for lines in split:
        kept = list(lines)
        for end in (-1, 0):
            if kept and kept[end].strip() and _line_key(kept[end]) in repeated:
                kept.pop(end)
        cleaned.append("\n".join(kept))
    return cleaned
