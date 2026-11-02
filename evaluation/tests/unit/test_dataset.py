import json
from pathlib import Path

import pytest

from explorai_eval.dataset import load_questions


def test_load_questions_returns_14() -> None:
    questions = load_questions()
    assert len(questions) == 14


def test_answerable_have_expected_quote() -> None:
    for q in load_questions():
        if q.paper != "none":
            assert q.expected_quote is not None


def test_unanswerable_have_no_expected_answer() -> None:
    for q in load_questions():
        if q.paper == "none":
            assert q.expected_answer is None


def test_answerable_have_expected_answer() -> None:
    answerable = [q for q in load_questions() if q.paper != "none"]
    assert all(q.expected_answer is not None for q in answerable)


def test_load_questions_missing_key_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"paper": "123", "expected_quote": None}]))
    with pytest.raises(ValueError):
        load_questions(bad)
