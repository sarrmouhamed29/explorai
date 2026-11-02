"""Markdown report renderer for evaluation results."""

from explorai_eval.metrics import ExistingMetrics, RagasMetrics


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0%}"


def render_markdown(
    existing: ExistingMetrics,
    ragas: RagasMetrics | None,
    *,
    provider: str,
    total_latency_s: float,
    total_input_tokens: int,
    total_output_tokens: int,
) -> str:
    lines: list[str] = [f"Provider: `{provider}`", ""]

    if provider == "fake":
        lines += [
            "> Fake providers on a tiny in-memory corpus: a plumbing check, "
            "not a quality measure.",
            "",
        ]

    lines += [
        "## Existing metrics",
        "",
        "| Metric | Rate | Count |",
        "|---|---|---|",
        f"| Retrieval hit rate | **{_fmt(existing.retrieval_hit_rate)}** "
        f"| {existing.retrieval_hits}/{existing.n_answerable} |",
        f"| Correct refusal rate | **{_fmt(existing.correct_refusal_rate)}** "
        f"| {existing.correct_refusals}/{existing.n_unanswerable} |",
        f"| False refusal rate | {_fmt(existing.false_refusal_rate)} "
        f"| {existing.false_refusals}/{existing.n_answerable} |",
        "",
    ]

    if existing.missed_questions:
        lines += ["Questions whose quote was not retrieved:", ""]
        lines += [f"- {q}" for q in existing.missed_questions]
        lines.append("")

    if ragas is not None:
        lines += [
            "## Ragas metrics (LLM-as-judge)",
            "",
            "| Metric | Score |",
            "|---|---|",
            f"| context_recall | {_fmt(ragas.context_recall)} |",
            f"| faithfulness | {_fmt(ragas.faithfulness)} |",
            f"| answer_correctness | {_fmt(ragas.answer_correctness)} |",
            f"| answer_relevancy | {_fmt(ragas.answer_relevancy)} |",
            "",
        ]

    lines += [
        "## Performance",
        "",
        "| | |",
        "|---|---|",
        f"| Total latency | {total_latency_s:.1f} s |",
        f"| Input tokens | {total_input_tokens} |",
        f"| Output tokens | {total_output_tokens} |",
        "",
    ]

    return "\n".join(lines)
