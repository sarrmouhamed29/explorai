"""The quality measurement: scoring, the run loop, the report and the fixtures."""

import json
import unicodedata
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from explorai_ai import measure
from explorai_ai.chunking import SectionInput, chunk_sections
from explorai_ai.citations import MIN_QUOTE_CHARS, MIN_QUOTE_WORDS, normalise_for_match
from explorai_ai.config import RagSettings
from explorai_ai.fakes import FakeGenerator
from explorai_ai.measure import (
    FIXTURE_PATH,
    InMemoryRetriever,
    MeasuredPipeline,
    Outcome,
    Question,
    Report,
    load_questions,
    render_markdown,
    run,
    score_refusal,
    score_retrieval,
)

QUOTE = "Each selected expert executes one forward pass."
CHUNKS = [
    "Sparse models route each token to a few experts. " + QUOTE,
    "Benchmarks grade every decision on the way to a verdict.",
    "Safety alignment is shallow: it concentrates in the first few output tokens.",
]
ANSWERED = "answered"
REFUSED = "insufficient_evidence"


def settings() -> RagSettings:
    return RagSettings(provider="fake", rewrite_enabled=False)


def pipeline() -> MeasuredPipeline:
    retriever = InMemoryRetriever(CHUNKS)
    return MeasuredPipeline(retriever, FakeGenerator(), settings())


# --- score_retrieval ---------------------------------------------------------


def test_retrieval_hit_when_the_quote_is_in_a_kept_chunk() -> None:
    assert score_retrieval(["unrelated text", CHUNKS[0]], QUOTE)


def test_retrieval_miss_when_no_kept_chunk_has_the_quote() -> None:
    assert not score_retrieval(CHUNKS[1:], QUOTE)
    assert not score_retrieval([], QUOTE)


def test_retrieval_ignores_case_and_whitespace() -> None:
    chunk = "Intro.\n  EACH   selected\texpert  executes\none forward   pass. Done."
    assert score_retrieval([chunk], QUOTE)


def test_retrieval_applies_unicode_compatibility_forms() -> None:
    # A ligature and a non-breaking space in the chunk, as a PDF extraction yields.
    ligature = chr(0xFB01)  # the "fi" ligature
    nbsp = chr(0xA0)
    chunk = f"The {ligature}rst result: one{nbsp}forward{nbsp}pass is enough."
    assert unicodedata.normalize("NFKC", chunk) != chunk
    assert score_retrieval([chunk], f"{ligature}rst result: one forward pass")
    assert score_retrieval([chunk], "first RESULT: one forward pass")


def test_retrieval_does_not_match_a_different_wording() -> None:
    assert not score_retrieval([CHUNKS[0]], "Each expert executes one forward pass.")


# --- score_refusal -----------------------------------------------------------


def test_refusal_is_correct_when_an_unanswerable_question_is_refused() -> None:
    assert score_refusal(REFUSED, None)


def test_refusal_is_incorrect_when_an_unanswerable_question_is_answered() -> None:
    assert not score_refusal(ANSWERED, None)


def test_answerable_question_is_correct_when_answered() -> None:
    assert score_refusal(ANSWERED, QUOTE)


def test_answerable_question_is_incorrect_when_refused() -> None:
    assert not score_refusal(REFUSED, QUOTE)


# --- run ---------------------------------------------------------------------


def test_run_counts_hits_and_refusals_with_the_fake_providers() -> None:
    questions = [
        Question("Does each selected expert execute a forward pass?", "p", QUOTE),
        Question(
            "Which output tokens does safety alignment concentrate in?",
            "p",
            "it concentrates in the first few output tokens",
        ),
        Question("Who won the football world cup final?", "p", None),
    ]
    report = run(questions, pipeline())
    assert report.n_answerable == 2
    assert report.n_unanswerable == 1
    assert report.retrieval_hits == 2
    assert report.retrieval_hit_rate == 1.0
    assert report.correct_refusals == 1
    assert report.correct_refusal_rate == 1.0
    assert report.false_refusals == 0
    assert report.missed_questions == []


def test_run_records_a_miss_and_a_wrong_answer() -> None:
    questions = [
        # The quote is not in the corpus: the retrieval cannot hit.
        Question(
            "Does each selected expert execute a forward pass?",
            "p",
            "a sentence the corpus does not contain",
        ),
        # Words shared with a chunk: the fake generator answers instead of refusing.
        Question("Does each selected expert execute one forward pass?", "p", None),
    ]
    report = run(questions, pipeline())
    assert report.retrieval_hits == 0
    assert report.retrieval_hit_rate == 0.0
    assert report.correct_refusals == 0
    assert report.correct_refusal_rate == 0.0
    assert report.missed_questions == [questions[0].question]


def test_run_on_an_empty_list_does_not_divide_by_zero() -> None:
    report = run([], pipeline())
    assert report.n_answerable == 0
    assert report.n_unanswerable == 0
    assert report.retrieval_hit_rate is None
    assert report.correct_refusal_rate is None
    assert "n/a" in render_markdown(report)


def test_run_only_scores_what_the_pipeline_reports() -> None:
    class Stub:
        def ask(self, question: str) -> Outcome:
            return Outcome(REFUSED, [QUOTE])

    questions = [Question("q1", "p", QUOTE), Question("q2", "p", None)]
    report = run(questions, Stub())
    # Retrieved the quote although the answer was refused: a hit and a false refusal.
    assert report.retrieval_hits == 1
    assert report.false_refusals == 1
    assert report.correct_refusals == 1


def test_measured_pipeline_exposes_the_kept_chunks_text() -> None:
    outcome = pipeline().ask("Does each selected expert execute a forward pass?")
    assert outcome.status == ANSWERED
    assert QUOTE in outcome.kept_texts[0]
    assert len(outcome.kept_texts) <= settings().final_candidates


# --- render_markdown ---------------------------------------------------------


def test_markdown_contains_the_rates_and_the_counts() -> None:
    report = Report(
        provider="real",
        retrieval_hits=7,
        n_answerable=9,
        correct_refusals=3,
        n_unanswerable=4,
        false_refusals=1,
        missed_questions=["What is X?", "What is Y?"],
    )
    markdown = render_markdown(report)
    assert "78%" in markdown and "7/9" in markdown
    assert "75%" in markdown and "3/4" in markdown
    assert "1/9" in markdown
    assert "What is X?" in markdown and "What is Y?" in markdown
    assert "`real`" in markdown


def test_markdown_flags_the_fake_provider_as_not_a_quality_measure() -> None:
    report = run([], pipeline())
    assert "plumbing" in render_markdown(replace(report, provider="fake"))


# --- the CLI -----------------------------------------------------------------


def test_cli_real_provider_without_keys_refuses_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert measure.main(["--provider", "real"]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err and "VOYAGE_API_KEY" in err
    assert "Traceback" not in err


def test_cli_fake_provider_runs_and_prints_a_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    target = tmp_path / "report.md"
    assert measure.main(["--provider", "fake", "--output", str(target)]) == 0
    out = capsys.readouterr().out
    assert "plumbing" in out
    assert target.read_text(encoding="utf-8").strip() == out.strip()


# --- the question file -------------------------------------------------------

ANSWERABLE = 10
UNANSWERABLE = 4
PAPERS = {"2610.40577", "2610.40619", "2610.40627"}


def test_question_file_has_the_agreed_shape() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert all(set(item) == {"question", "paper", "expected_quote"} for item in raw)
    questions = load_questions()
    assert len(questions) == ANSWERABLE + UNANSWERABLE
    answerable = [q for q in questions if q.expected_quote is not None]
    assert len(answerable) == ANSWERABLE
    assert len({q.question for q in questions}) == len(questions)
    assert {q.paper for q in answerable} == PAPERS


def test_every_quote_is_long_enough_to_be_a_citation() -> None:
    for q in load_questions():
        if q.expected_quote is None:
            continue
        normalised = normalise_for_match(q.expected_quote)
        assert len(normalised) >= MIN_QUOTE_CHARS
        assert len(normalised.split()) >= MIN_QUOTE_WORDS


def test_every_quote_appears_in_its_paper() -> None:
    # The text ingestion would store, extracted from the annotated PDFs of
    # sub-project 1. Skipped where the pipelines package is not installed.
    pdf = pytest.importorskip("explorai_pipelines.extract.pdf")
    root = Path(__file__).resolve().parents[3]
    annotated = root / "pipelines" / "tests" / "fixtures" / "annotated"
    defaults = RagSettings(provider="fake")
    texts: dict[str, list[str]] = {}
    for paper in PAPERS:
        document = pdf.extract_pdf((annotated / f"{paper}.pdf").read_bytes())
        chunks = chunk_sections(
            [SectionInput(s.heading, s.text) for s in document.sections],
            target_words=defaults.chunk_words,
            overlap_words=defaults.chunk_overlap_words,
            max_chunks=defaults.max_chunks_per_document,
        )
        texts[paper] = [normalise_for_match(c.text) for c in chunks]
    for q in load_questions():
        if q.expected_quote is None:
            continue
        needle = normalise_for_match(q.expected_quote)
        # Inside one chunk, as the retrieval metric needs.
        assert any(needle in text for text in texts[q.paper]), q.question


def test_load_questions_rejects_a_malformed_file(tmp_path: Path) -> None:
    bad = tmp_path / "q.json"
    bad.write_text(json.dumps([{"question": "x", "paper": "p"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="expected_quote"):
        load_questions(bad)


def test_user_id_of_the_measure_is_not_a_real_account() -> None:
    assert isinstance(measure.MEASURE_USER, uuid.UUID)
