"""PII detection engine: presidio backend with regex fallback.

The presidio backend is lazy-initialised on first use so the heavy spaCy model
does not load in processes that never scan.  When presidio is not installed (the
default in CI), the regex fallback covers the four most-common entity types.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PiiEntity:
    entity_type: str  # "EMAIL_ADDRESS", "PHONE_NUMBER", …
    start: int
    end: int
    score: float  # 0.0-1.0; regex fallback always returns 1.0


@dataclass(frozen=True)
class ScanResult:
    has_pii: bool
    entities: tuple[PiiEntity, ...]
    # original text when has_pii is False; [REDACTED_<TYPE>] placeholders when True
    redacted_text: str


# ---------------------------------------------------------------------------
# Regex fallback patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?[0-9]{1,3}[\s\-.]?)?(?:\(?[0-9]{2,4}\)?[\s\-.]?){2,}[0-9]{2,4}(?!\d)",
)
_CC_RE = re.compile(r"\b(?:\d[\s\-]?){15,16}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,}\b")

_REGEX_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL_ADDRESS", _EMAIL_RE),
    ("PHONE_NUMBER", _PHONE_RE),
    ("CREDIT_CARD", _CC_RE),
    ("IBAN_CODE", _IBAN_RE),
]


# Luhn algorithm for credit card validation
def _luhn_ok(digits: str) -> bool:
    clean = digits.replace(" ", "").replace("-", "")
    if not clean.isdigit():
        return False
    total = 0
    reverse = clean[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _scan_regex(text: str) -> list[PiiEntity]:
    entities: list[PiiEntity] = []
    for entity_type, pattern in _REGEX_PATTERNS:
        for m in pattern.finditer(text):
            if entity_type == "CREDIT_CARD":
                digits = m.group().replace(" ", "").replace("-", "")
                if len(digits) not in (15, 16) or not _luhn_ok(digits):
                    continue
            if entity_type == "IBAN_CODE":
                raw = m.group()
                if len(raw.replace(" ", "")) < 12:
                    continue
            entities.append(
                PiiEntity(
                    entity_type=entity_type,
                    start=m.start(),
                    end=m.end(),
                    score=1.0,
                )
            )
    return entities


# ---------------------------------------------------------------------------
# Presidio backend (optional)
# ---------------------------------------------------------------------------

_presidio_available: bool | None = None  # None = not yet checked

_PRESIDIO_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "DATE_TIME",
    "PERSON",
    "LOCATION",
    "NRP",
]


def _is_presidio_available() -> bool:
    global _presidio_available
    if _presidio_available is None:
        try:
            import presidio_analyzer  # noqa: F401

            _presidio_available = True
        except ImportError:
            _presidio_available = False
    return _presidio_available


_presidio_analyzer: object | None = None
_presidio_anonymizer: object | None = None


def _get_presidio() -> tuple[object, object]:
    global _presidio_analyzer, _presidio_anonymizer
    if _presidio_analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine

        _presidio_analyzer = AnalyzerEngine()
        _presidio_anonymizer = AnonymizerEngine()
    return _presidio_analyzer, _presidio_anonymizer


def _scan_presidio(text: str, language: str) -> list[PiiEntity]:
    analyzer, _ = _get_presidio()
    from presidio_analyzer import AnalyzerEngine

    assert isinstance(analyzer, AnalyzerEngine)
    results = analyzer.analyze(
        text=text, language=language, entities=_PRESIDIO_ENTITIES
    )
    return [
        PiiEntity(
            entity_type=r.entity_type,
            start=r.start,
            end=r.end,
            score=r.score,
        )
        for r in results
    ]


def _redact(text: str, entities: list[PiiEntity]) -> str:
    """Replace entity spans with [REDACTED_<TYPE>] markers (right to left)."""
    # Sort by start descending so replacements don't shift later offsets.
    for entity in sorted(entities, key=lambda e: e.start, reverse=True):
        placeholder = f"[REDACTED_{entity.entity_type}]"
        text = text[: entity.start] + placeholder + text[entity.end :]
    return text


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


class PiiScanner:
    """Thread-safe scanner; share one instance per process.

    Automatically selects presidio when installed; falls back to regex otherwise.
    The presidio engine is created on first use (lazy init).
    """

    def scan(self, text: str, language: str = "en") -> ScanResult:
        if not text:
            return ScanResult(has_pii=False, entities=(), redacted_text="")

        # NFKC-normalise so Unicode homoglyphs fold to ASCII before scanning
        normalised = unicodedata.normalize("NFKC", text)

        if _is_presidio_available():
            raw_entities = _scan_presidio(normalised, language)
        else:
            raw_entities = _scan_regex(normalised)

        if not raw_entities:
            return ScanResult(has_pii=False, entities=(), redacted_text=text)

        redacted = _redact(normalised, raw_entities)
        return ScanResult(
            has_pii=True,
            entities=tuple(raw_entities),
            redacted_text=redacted,
        )
