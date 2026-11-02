"""Evaluate RAG quality and optionally send traces to Langfuse.

    uv run python -m explorai_eval.measure --provider real|fake [--output PATH]

``--provider fake`` runs the pipeline on a tiny in-memory corpus as a
plumbing check. ``--provider real`` calls Voyage and Anthropic (costs money)
and computes Ragas metrics (LLM-as-judge).
"""

import argparse
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from explorai_ai.errors import ProviderError
from explorai_eval.config import EvalSettings
from explorai_eval.dataset import FIXTURE_PATH, load_questions
from explorai_eval.langfuse_client import LangfuseClient
from explorai_eval.metrics import (
    ExistingMetrics,
    RagasMetrics,
    compute_existing_metrics,
)
from explorai_eval.report import render_markdown
from explorai_eval.runner import EvalSample, run_evaluation


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError, ValueError):
        return "unknown"


def _missing_settings(error: ValidationError) -> str:
    return "; ".join(str(item["msg"]) for item in error.errors())


def _build_fake(settings: EvalSettings, corpus: list[str]) -> Any:
    from explorai_ai.fakes import FakeGenerator
    from explorai_ai.measure import InMemoryRetriever, MeasuredPipeline

    return MeasuredPipeline(InMemoryRetriever(corpus), FakeGenerator(), settings)


def _build_real(settings: EvalSettings) -> Any:
    import os

    from qdrant_client import QdrantClient

    from explorai_ai.factory import build_providers
    from explorai_ai.index import QdrantIndex
    from explorai_ai.measure import MeasuredPipeline
    from explorai_ai.retriever import Retriever
    from explorai_ai.store import ChunkStore
    from explorai_db.rls import ScopedSessions
    from explorai_db.session import make_engine, make_session_factory

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to read the chunk texts")

    api_key = settings.qdrant_api_key
    client = QdrantClient(
        url=settings.qdrant_url,
        api_key=api_key.get_secret_value() if api_key else None,
        timeout=max(1, round(settings.provider_timeout_seconds + 0.5)),
        check_compatibility=False,
    )
    index = QdrantIndex(client)
    if not index.collection_exists(settings.index_name):
        raise RuntimeError(
            "the Qdrant index is empty: ingest the annotated papers and run "
            "'index-pending' first (see docs/evaluation/rag-baseline.md)"
        )
    providers = build_providers(settings)
    store = ChunkStore(ScopedSessions(make_session_factory(make_engine(database_url))))
    retriever = Retriever(
        index=index,
        embedder=providers.embedder,
        reranker=providers.reranker,
        generator=providers.generator,
        store=store,
        settings=settings,
    )
    return MeasuredPipeline(retriever, providers.generator, settings)


def _scores(existing: ExistingMetrics, ragas: RagasMetrics | None) -> dict[str, float]:
    result: dict[str, float] = {}
    if existing.retrieval_hit_rate is not None:
        result["retrieval_hit_rate"] = existing.retrieval_hit_rate
    if existing.correct_refusal_rate is not None:
        result["correct_refusal_rate"] = existing.correct_refusal_rate
    if ragas is not None:
        for name, val in [
            ("context_recall", ragas.context_recall),
            ("faithfulness", ragas.faithfulness),
            ("answer_correctness", ragas.answer_correctness),
            ("answer_relevancy", ragas.answer_relevancy),
        ]:
            if val is not None:
                result[name] = val
    return result


def main(argv: Sequence[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else None
    )
    parser.add_argument("--provider", choices=["real", "fake"], required=True)
    parser.add_argument("--questions", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--output", type=Path, help="also write the markdown here")
    parser.add_argument(
        "--langfuse", action="store_true", help="enable Langfuse tracing"
    )
    parser.add_argument("--dataset-name", default="explorai-eval")
    args = parser.parse_args(argv)

    os.environ["RAG_PROVIDER"] = args.provider
    try:
        settings = EvalSettings.from_env()
    except ValidationError as error:
        print(
            f"cannot start the measurement: {_missing_settings(error)}", file=sys.stderr
        )
        return 2

    try:
        questions = load_questions(args.questions)
    except (ValueError, FileNotFoundError) as error:
        print(f"cannot load questions: {error}", file=sys.stderr)
        return 2

    run_name = f"{_git_sha()}-{int(time.time())}"
    langfuse = LangfuseClient(
        settings, args.dataset_name, run_name, enabled=args.langfuse
    )

    try:
        if args.provider == "fake":
            corpus = [
                q.expected_quote for q in questions if q.expected_quote is not None
            ]
            pipeline = _build_fake(settings, corpus)
        else:
            pipeline = _build_real(settings)

        samples: list[EvalSample] = run_evaluation(questions, pipeline)
    except RuntimeError as error:
        print(f"cannot start the measurement: {error}", file=sys.stderr)
        return 2
    except ProviderError as error:
        print(f"the measurement stopped: {type(error).__name__}", file=sys.stderr)
        return 1

    existing = compute_existing_metrics(samples)

    ragas: RagasMetrics | None = None
    if args.provider == "real":
        try:
            from explorai_eval.metrics import compute_ragas_metrics

            ragas = compute_ragas_metrics(samples, settings)
        except Exception as error:  # noqa: BLE001
            print(f"Ragas evaluation failed: {error}", file=sys.stderr)
            ragas = None

    scores = _scores(existing, ragas)
    for sample in samples:
        langfuse.record_sample(sample, scores)
    langfuse.flush()

    total_latency = sum(s.latency_s for s in samples)
    total_input = sum(s.input_tokens for s in samples)
    total_output = sum(s.output_tokens for s in samples)

    markdown = render_markdown(
        existing,
        ragas,
        provider=args.provider,
        total_latency_s=total_latency,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
    )
    print(markdown)
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
