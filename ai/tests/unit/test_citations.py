import uuid

import pytest

from explorai_ai.citations import Citation, normalise_for_match, verify_citations
from explorai_ai.models import RawCitation
from explorai_ai.retriever import Candidate

LEFT_DOUBLE = chr(0x201C)
RIGHT_DOUBLE = chr(0x201D)
ELLIPSIS = chr(0x2026)
NBSP = chr(0x00A0)
LIGATURE_FI = chr(0xFB01)


def candidate(text: str, title: str | None = "Paper") -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        document_title=title,
        heading="Method",
        text=text,
        score=0.9,
    )


SOURCES = [
    candidate("Transformers rely on self-attention. They drop recurrence."),
    candidate("The model was trained on 8 GPUs for 3 days.", title=None),
]


def test_exact_quote_is_kept() -> None:
    [kept] = verify_citations([RawCitation(2, "trained on 8 GPUs")], SOURCES)
    source = SOURCES[1]
    assert kept == Citation(
        source=2,
        quote="trained on 8 GPUs",
        chunk_id=source.chunk_id,
        document_id=source.document_id,
        document_title=None,
        heading="Method",
    )


def test_quote_matches_across_whitespace_and_case() -> None:
    quote = "TRANSFORMERS  rely\non" + NBSP + "self-attention"
    [kept] = verify_citations([RawCitation(1, quote)], SOURCES)
    assert kept.source == 1
    # The quote shown to the user keeps the model's words, whitespace collapsed.
    assert kept.quote == "TRANSFORMERS rely on self-attention"


def test_quote_matches_after_unicode_compatibility_folding() -> None:
    sources = [candidate("A " + LIGATURE_FI + "ne result.")]
    assert verify_citations([RawCitation(1, "a fine result")], sources) != []


@pytest.mark.parametrize(
    "wrapped",
    [
        '"they drop recurrence"',
        "'they drop recurrence'",
        LEFT_DOUBLE + "they drop recurrence" + RIGHT_DOUBLE,
        ELLIPSIS + "they drop recurrence" + ELLIPSIS,
        "...they drop recurrence...",
        ' " ... they drop recurrence ... " ',
    ],
)
def test_surrounding_quotes_and_ellipses_are_ignored(wrapped: str) -> None:
    [kept] = verify_citations([RawCitation(1, wrapped)], SOURCES)
    assert kept.quote == "they drop recurrence"


def test_quote_not_in_source_is_dropped() -> None:
    raw = [
        RawCitation(1, "Transformers rely on convolutions"),
        # Right text, wrong source: verification is per cited chunk.
        RawCitation(1, "trained on 8 GPUs"),
    ]
    assert verify_citations(raw, SOURCES) == []


@pytest.mark.parametrize(
    "quote",
    [
        # An inner ellipsis stitches two passages together: not verbatim.
        "Transformers ... recurrence",
        "Transformers rely on self attention",  # hyphen dropped
        "Transformers rely on self-attention!",  # punctuation added
    ],
)
def test_near_misses_are_dropped(quote: str) -> None:
    assert verify_citations([RawCitation(1, quote)], SOURCES) == []


@pytest.mark.parametrize("source", [0, -1, 3, 99])
def test_out_of_range_source_is_dropped(source: int) -> None:
    # "the" occurs in every source: only the source number can reject it (0 and -1
    # must not wrap around to the last source, Python-style).
    assert all("the" in normalise_for_match(c.text) for c in SOURCES)
    assert verify_citations([RawCitation(source, "the")], SOURCES) == []


@pytest.mark.parametrize(
    "quote", ["", "   ", '""', "...", ELLIPSIS, " ' ", "on", ' "On" ']
)
def test_empty_quote_is_dropped(quote: str) -> None:
    # Empty after stripping, or shorter than three characters: it proves nothing.
    assert verify_citations([RawCitation(1, quote)], SOURCES) == []
    assert verify_citations([RawCitation(2, quote)], SOURCES) == []


def test_three_characters_is_enough() -> None:
    assert verify_citations([RawCitation(2, "GPU")], SOURCES) != []


def test_duplicates_are_merged() -> None:
    raw = [
        RawCitation(1, "self-attention"),
        RawCitation(1, "  SELF-ATTENTION "),
        RawCitation(2, "8 GPUs"),
        RawCitation(1, "self-attention"),
        RawCitation(1, "drop recurrence"),
    ]
    kept = verify_citations(raw, SOURCES)
    assert [(c.source, c.quote) for c in kept] == [
        (1, "self-attention"),
        (2, "8 GPUs"),
        (1, "drop recurrence"),
    ]


def test_no_candidates_means_no_citation() -> None:
    assert verify_citations([RawCitation(1, "anything")], []) == []


def test_normalise_for_match() -> None:
    assert normalise_for_match("  A\tB" + NBSP + "\nc  ") == "a b c"
    assert normalise_for_match("Stra" + chr(0x00DF) + "e") == "strasse"
    assert normalise_for_match(LIGATURE_FI) == "fi"
