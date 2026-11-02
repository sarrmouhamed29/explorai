"""Unit tests for OutputFilter: PII in generated answers and citation quotes."""

import uuid

from explorai_ai.citations import Citation
from explorai_ai.pii import PiiScanner
from explorai_ai.safety_output import OutputFilter


def _citation(quote: str) -> Citation:
    return Citation(
        source=1,
        quote=quote,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="T",
        heading="H",
    )


def test_clean_answer_passes() -> None:
    f = OutputFilter(PiiScanner())
    result = f.check("Transformers use attention mechanisms.", [])
    assert result.safe is True
    assert result.redacted_answer is None


def test_email_in_answer_is_caught() -> None:
    f = OutputFilter(PiiScanner())
    result = f.check("Contact alice@example.com for details.", [])
    assert result.safe is False


def test_pii_in_citation_quote_is_caught() -> None:  # Review Focus #2
    f = OutputFilter(PiiScanner())
    result = f.check("See citation [1].", [_citation("Email john@example.com")])
    assert result.safe is False


def test_redacted_marker_in_answer_is_caught() -> None:  # Review Focus #4
    f = OutputFilter(PiiScanner())
    result = f.check("Contact [REDACTED_EMAIL_ADDRESS] for info.", [])
    assert result.safe is False
