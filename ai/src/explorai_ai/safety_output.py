"""Output filter: scan generated answers and citation quotes for PII.

Three checks in one pass:
1. Scan the answer text with ``PiiScanner``.
2. Scan each citation quote with ``PiiScanner``.
3. Detect residual ``[REDACTED_*]`` markers in the answer — the model may
   quote a redacted chunk verbatim, leaking the marker pattern itself.

Any positive result returns ``safe=False``; the pipeline treats that as an
``insufficient_evidence`` refusal so no PII-bearing text reaches the client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from explorai_ai.citations import Citation
from explorai_ai.pii import PiiScanner

_REDACTED_MARKER_RE = re.compile(r"\[REDACTED_[A-Z_]+\]")


@dataclass(frozen=True)
class OutputFilterResult:
    safe: bool
    redacted_answer: str | None  # reserved for future redact mode; always None for now


class OutputFilter:
    """Scans generated answers and citation quotes for PII.

    Thread-safe; share one instance per process.
    """

    def __init__(self, scanner: PiiScanner) -> None:
        self._scanner = scanner

    def check(self, answer: str, citations: list[Citation]) -> OutputFilterResult:
        # Check 1: PII in the answer text
        if self._scanner.scan(answer).has_pii:
            return OutputFilterResult(safe=False, redacted_answer=None)

        # Check 2: residual [REDACTED_*] markers in the answer
        if _REDACTED_MARKER_RE.search(answer):
            return OutputFilterResult(safe=False, redacted_answer=None)

        # Check 3: PII in any citation quote
        for citation in citations:
            if self._scanner.scan(citation.quote).has_pii:
                return OutputFilterResult(safe=False, redacted_answer=None)

        return OutputFilterResult(safe=True, redacted_answer=None)
