from explorai_eval.metrics import ExistingMetrics, RagasMetrics
from explorai_eval.report import render_markdown


def _existing(
    *,
    hit_rate: float | None = 0.8,
    refusal_rate: float | None = 1.0,
    false_refusal_rate: float | None = 0.0,
) -> ExistingMetrics:
    return ExistingMetrics(
        retrieval_hit_rate=hit_rate,
        correct_refusal_rate=refusal_rate,
        false_refusal_rate=false_refusal_rate,
        retrieval_hits=8,
        n_answerable=10,
        correct_refusals=4,
        n_unanswerable=4,
        false_refusals=0,
    )


def test_render_includes_provider_name() -> None:
    md = render_markdown(
        _existing(),
        None,
        provider="fake",
        total_latency_s=1.0,
        total_input_tokens=10,
        total_output_tokens=5,
    )
    assert "fake" in md


def test_render_none_rate_as_na() -> None:
    existing = _existing(hit_rate=None)
    md = render_markdown(
        existing,
        None,
        provider="fake",
        total_latency_s=1.0,
        total_input_tokens=10,
        total_output_tokens=5,
    )
    assert "n/a" in md


def test_render_ragas_section_absent_when_none() -> None:
    md = render_markdown(
        _existing(),
        None,
        provider="fake",
        total_latency_s=1.0,
        total_input_tokens=10,
        total_output_tokens=5,
    )
    assert "context_recall" not in md


def test_render_ragas_section_present() -> None:
    ragas = RagasMetrics(
        context_recall=0.9,
        faithfulness=0.85,
        answer_correctness=0.8,
        answer_relevancy=0.95,
    )
    md = render_markdown(
        _existing(),
        ragas,
        provider="real",
        total_latency_s=1.0,
        total_input_tokens=10,
        total_output_tokens=5,
    )
    assert "context_recall" in md
    assert "faithfulness" in md


def test_render_nan_ragas_as_na() -> None:
    ragas = RagasMetrics(
        context_recall=None,
        faithfulness=None,
        answer_correctness=None,
        answer_relevancy=None,
    )
    md = render_markdown(
        _existing(),
        ragas,
        provider="real",
        total_latency_s=1.0,
        total_input_tokens=10,
        total_output_tokens=5,
    )
    assert "n/a" in md


def test_render_missed_questions_appear() -> None:
    existing = ExistingMetrics(
        retrieval_hit_rate=0.5,
        correct_refusal_rate=None,
        false_refusal_rate=0.5,
        retrieval_hits=5,
        n_answerable=10,
        correct_refusals=0,
        n_unanswerable=0,
        false_refusals=5,
        missed_questions=["What is alpha?", "What is beta?"],
    )
    md = render_markdown(
        existing,
        None,
        provider="real",
        total_latency_s=1.0,
        total_input_tokens=10,
        total_output_tokens=5,
    )
    assert "What is alpha?" in md
    assert "What is beta?" in md


def test_render_latency_appears() -> None:
    md = render_markdown(
        _existing(),
        None,
        provider="fake",
        total_latency_s=12.5,
        total_input_tokens=100,
        total_output_tokens=50,
    )
    assert "12" in md
