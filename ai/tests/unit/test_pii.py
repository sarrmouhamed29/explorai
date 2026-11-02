"""Unit tests for the PII detection engine (regex fallback path; no presidio in CI)."""

from explorai_ai.pii import PiiScanner


def test_scan_email_detected() -> None:
    scanner = PiiScanner()
    result = scanner.scan("Contact me at alice@example.com for details.")
    assert result.has_pii is True
    assert any(e.entity_type == "EMAIL_ADDRESS" for e in result.entities)
    assert "[REDACTED_EMAIL_ADDRESS]" in result.redacted_text


def test_scan_clean_text_not_flagged() -> None:
    scanner = PiiScanner()
    result = scanner.scan("The transformer architecture was introduced in 2017.")
    assert result.has_pii is False
    assert (
        result.redacted_text == "The transformer architecture was introduced in 2017."
    )


def test_scan_empty_string() -> None:
    scanner = PiiScanner()
    result = scanner.scan("")
    assert result.has_pii is False
    assert result.entities == ()
    assert result.redacted_text == ""


def test_scan_phone_detected() -> None:
    scanner = PiiScanner()
    result = scanner.scan("Call me at +33612345678.")
    assert result.has_pii is True


def test_scan_credit_card_detected() -> None:
    scanner = PiiScanner()
    result = scanner.scan("Card number 4111111111111111 expired.")
    assert result.has_pii is True
