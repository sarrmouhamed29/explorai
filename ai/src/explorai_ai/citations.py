"""Citation verification: every quote the model gives is checked by our code.

The model is asked to back each claim with a verbatim quote of a numbered source.
Asking is not proof: a model can paraphrase, misnumber a source or invent a quote
(and a prompt injection can push it to). So a citation survives only if its quote
really occurs in the chunk it names. Matching tolerates what does not change the
words: Unicode compatibility forms (ligatures, non-breaking spaces), case and runs
of whitespace, plus the quotation marks or ellipses a model tends to wrap a quote
in. Anything else (a dropped hyphen, an inner "..." stitching two passages, an
added word) fails: the check is deliberately conservative.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from explorai_ai.models import RawCitation

if TYPE_CHECKING:
    from explorai_ai.retriever import Candidate

MIN_QUOTE_WORDS = 2
MIN_QUOTE_CHARS = 10
"""A quote counts only with at least 2 words and 10 characters once normalised.

"the" or "and the" occur in almost any chunk, so they would let a lazy model (or a
chunk carrying an injected instruction to cite "the") turn any answer into a
verified one. A single word, however long, is a keyword, not a quote.
"""

# Marks a model may put around a quote: straight and curly quotes, the ellipsis
# character and three dots. Only stripped at the two ends, never inside.
_MARKS = "".join(map(chr, (0x22, 0x27, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026)))
_MARK = r"(?:\s|\.\.\.|[" + re.escape(_MARKS) + "])"
_EDGE = re.compile(f"^{_MARK}+|{_MARK}+$")


@dataclass(frozen=True)
class Citation:
    source: int
    quote: str
    chunk_id: UUID
    document_id: UUID
    document_title: str | None
    heading: str | None


def normalise_for_match(text: str) -> str:
    """Unicode NFKC, case-folded, every whitespace run collapsed to one space."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(folded.split())


def _strip_edges(text: str) -> str:
    return _EDGE.sub("", text)


def verify_citations(
    raw: Sequence[RawCitation], candidates: Sequence["Candidate"]
) -> list[Citation]:
    """The citations whose quote occurs in the candidate they cite, in order.

    ``source`` is the 1-based position of the cited chunk in ``candidates`` (the
    numbering of the prompt). A citation is dropped when the source number is out
    of range or when its quote, once normalised and stripped of surrounding quote
    marks and ellipses, has fewer than ``MIN_QUOTE_WORDS`` words or
    ``MIN_QUOTE_CHARS`` characters, or is absent from the normalised chunk text.
    Repeats of the same (source, normalised quote) are kept once, at their first
    position. The returned quote is the model's own text, whitespace collapsed
    and stripped of those surrounding marks.
    """
    kept: list[Citation] = []
    seen: set[tuple[int, str]] = set()
    haystacks: dict[int, str] = {}
    for citation in raw:
        if not 1 <= citation.source <= len(candidates):
            continue
        shown = _strip_edges(" ".join(citation.quote.split()))
        # Normalising may create new edge marks (a fullwidth quote becomes '"').
        needle = _strip_edges(normalise_for_match(shown))
        if len(needle) < MIN_QUOTE_CHARS or len(needle.split()) < MIN_QUOTE_WORDS:
            continue
        key = (citation.source, needle)
        if key in seen:
            continue
        candidate = candidates[citation.source - 1]
        if citation.source not in haystacks:
            haystacks[citation.source] = normalise_for_match(candidate.text)
        if needle not in haystacks[citation.source]:
            continue
        seen.add(key)
        kept.append(
            Citation(
                source=citation.source,
                quote=shown,
                chunk_id=candidate.chunk_id,
                document_id=candidate.document_id,
                document_title=candidate.document_title,
                heading=candidate.heading,
            )
        )
    return kept
