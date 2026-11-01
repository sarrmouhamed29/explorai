"""Measure RAG quality on hand-annotated questions, on demand.

    uv run python -X utf8 -m explorai_ai.measure --provider real|fake [--output PATH]

The question file lists, per question, the paper it comes from and a verbatim
quote that answers it (``expected_quote``), or ``null`` when the corpus does not
answer it. Two rates are reported:

* ``retrieval_hit_rate``: over the answerable questions, the share whose quote
  appears (normalised like the citation verifier does) in at least one of the
  chunks the generator is shown. It is a recall of the evidence: it says nothing
  about the quality of the generated answer.
* ``correct_refusal_rate``: over the unanswerable questions, the share the
  pipeline refuses (``insufficient_evidence``). ``false_refusals`` counts the
  answerable questions it refused although it should have answered.

``--provider real`` calls Voyage and Anthropic (it costs money) on the public
corpus already indexed in Qdrant, so it needs the keys, ``QDRANT_URL`` and
``DATABASE_URL`` in the environment. Agents and CI never run it. ``--provider
fake`` runs the same code on a tiny in-memory corpus: a plumbing check whose
numbers mean nothing.
"""

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from explorai_ai.citations import normalise_for_match
from explorai_ai.config import RagSettings
from explorai_ai.errors import ProviderError, RetrievalUnavailable
from explorai_ai.fakes import FakeEmbedder, FakeGenerator, FakeReranker
from explorai_ai.pipeline import AnswerPipeline, Retrieves
from explorai_ai.providers.base import Generator
from explorai_ai.retriever import Candidate, RetrievalResult
from explorai_ai.scope import Scope

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "questions"
    / "questions.json"
)
REFUSED = "insufficient_evidence"
ANSWERED = "answered"
MEASURE_USER = uuid.UUID("00000000-0000-0000-0000-00000000e0a1")
"""Identity of the measurement: it asks the public corpus, no private row."""


@dataclass(frozen=True)
class Question:
    question: str
    paper: str
    expected_quote: str | None


@dataclass(frozen=True)
class Outcome:
    """What the measurement needs from one ask: the status and the kept chunks."""

    status: str
    kept_texts: list[str]


class Asks(Protocol):
    def ask(self, question: str) -> Outcome: ...


@dataclass(frozen=True)
class Report:
    provider: str
    retrieval_hits: int
    n_answerable: int
    correct_refusals: int
    n_unanswerable: int
    false_refusals: int
    missed_questions: list[str]

    @property
    def retrieval_hit_rate(self) -> float | None:
        return _rate(self.retrieval_hits, self.n_answerable)

    @property
    def correct_refusal_rate(self) -> float | None:
        return _rate(self.correct_refusals, self.n_unanswerable)


def _rate(ok: int, total: int) -> float | None:
    return ok / total if total else None


def load_questions(path: Path = FIXTURE_PATH) -> list[Question]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions: list[Question] = []
    for item in raw:
        for key in ("question", "paper", "expected_quote"):
            if key not in item:
                raise ValueError(f"question entry without {key!r}: {item}")
        questions.append(
            Question(item["question"], item["paper"], item["expected_quote"])
        )
    return questions


def score_retrieval(candidates_text: Sequence[str], expected_quote: str) -> bool:
    """True when the normalised quote occurs in at least one kept chunk."""
    needle = normalise_for_match(expected_quote)
    return any(needle in normalise_for_match(text) for text in candidates_text)


def score_refusal(status: str, expected_quote: str | None) -> bool:
    """True when the pipeline did the right thing: refuse iff nothing answers."""
    if expected_quote is None:
        return status == REFUSED
    return status == ANSWERED


def run(
    questions: Sequence[Question], pipeline: Asks, provider: str = "fake"
) -> Report:
    hits = correct_refusals = false_refusals = n_answerable = n_unanswerable = 0
    missed: list[str] = []
    for item in questions:
        outcome = pipeline.ask(item.question)
        if item.expected_quote is None:
            n_unanswerable += 1
            correct_refusals += score_refusal(outcome.status, None)
            continue
        n_answerable += 1
        if score_retrieval(outcome.kept_texts, item.expected_quote):
            hits += 1
        else:
            missed.append(item.question)
        false_refusals += not score_refusal(outcome.status, item.expected_quote)
    return Report(
        provider=provider,
        retrieval_hits=hits,
        n_answerable=n_answerable,
        correct_refusals=correct_refusals,
        n_unanswerable=n_unanswerable,
        false_refusals=false_refusals,
        missed_questions=missed,
    )


class _Recording:
    """Wraps a retriever and keeps its last result: the pipeline hides it."""

    def __init__(self, inner: Retrieves) -> None:
        self._inner = inner
        self.last: RetrievalResult | None = None

    def retrieve(
        self,
        user_id: uuid.UUID,
        question: str,
        scope: Scope = "all",
        document_id: uuid.UUID | None = None,
    ) -> RetrievalResult:
        self.last = self._inner.retrieve(user_id, question, scope, document_id)
        return self.last


class MeasuredPipeline:
    """An ``AnswerPipeline`` that also reports the chunks the generator was shown.

    ``AnswerResult`` only carries verified citations, not the kept chunks, so the
    retriever is wrapped instead of changing the pipeline.
    """

    def __init__(
        self,
        retriever: Retrieves,
        generator: Generator,
        settings: RagSettings,
        *,
        scope: Scope = "public",
    ) -> None:
        self._recording = _Recording(retriever)
        self._pipeline = AnswerPipeline(self._recording, generator, settings)
        self._final = settings.final_candidates
        self._scope: Scope = scope

    def ask(self, question: str) -> Outcome:
        self._recording.last = None
        result = self._pipeline.ask(MEASURE_USER, question, self._scope)
        retrieved = self._recording.last
        # The pipeline shows the generator at most final_candidates chunks.
        kept: list[Candidate] = []
        if retrieved is not None:
            kept = retrieved.candidates[: self._final]
        return Outcome(result.status, [candidate.text for candidate in kept])


class InMemoryRetriever:
    """Dense ranking by the fake embedder, then the fake reranker: no services."""

    def __init__(self, chunks: Sequence[str], dimension: int = 64) -> None:
        self._embedder = FakeEmbedder(dimension)
        self._reranker = FakeReranker()
        self._chunks = list(chunks)
        self._vectors = self._embedder.embed_documents(self._chunks)

    def retrieve(
        self,
        user_id: uuid.UUID,
        question: str,
        scope: Scope = "all",
        document_id: uuid.UUID | None = None,
    ) -> RetrievalResult:
        query = self._embedder.embed_query(question)
        order = sorted(
            range(len(self._chunks)),
            key=lambda i: (
                -sum(a * b for a, b in zip(query, self._vectors[i], strict=True)),
                i,
            ),
        )
        texts = [self._chunks[i] for i in order]
        hits = self._reranker.rerank(question, texts, len(texts))
        candidates = [
            Candidate(
                chunk_id=uuid.uuid5(uuid.NAMESPACE_URL, texts[hit.index]),
                document_id=uuid.UUID(int=0),
                version_id=uuid.UUID(int=0),
                document_title=None,
                heading=None,
                text=texts[hit.index],
                score=hit.score,
            )
            for hit in hits
        ]
        return RetrievalResult(candidates, candidates[0].score if candidates else None)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(report: Report) -> str:
    lines = [f"Provider: `{report.provider}`", ""]
    if report.provider == "fake":
        lines += [
            "> Fake providers on a tiny in-memory corpus: a plumbing check, "
            "not a quality measure.",
            "",
        ]
    lines += [
        "| Metric | Rate | Count |",
        "|---|---|---|",
        f"| Retrieval hit rate (quote in the kept chunks) "
        f"| **{_percent(report.retrieval_hit_rate)}** "
        f"| {report.retrieval_hits}/{report.n_answerable} |",
        f"| Correct refusals (unanswerable questions) "
        f"| **{_percent(report.correct_refusal_rate)}** "
        f"| {report.correct_refusals}/{report.n_unanswerable} |",
        f"| False refusals (answerable questions refused) "
        f"| {_percent(_rate(report.false_refusals, report.n_answerable))} "
        f"| {report.false_refusals}/{report.n_answerable} |",
        "",
    ]
    if report.missed_questions:
        lines += ["Questions whose quote was not retrieved:", ""]
        lines += [f"- {question}" for question in report.missed_questions]
        lines.append("")
    return "\n".join(lines)


def _missing_settings(error: ValidationError) -> str:
    # hide_input_in_errors: messages never echo a value, only what is wrong.
    return "; ".join(str(item["msg"]) for item in error.errors())


def _build_real(settings: RagSettings) -> MeasuredPipeline:
    from qdrant_client import QdrantClient

    from explorai_ai.factory import build_providers
    from explorai_ai.index import QdrantIndex
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


def _build_fake(
    settings: RagSettings, questions: Sequence[Question]
) -> MeasuredPipeline:
    corpus = [q.expected_quote for q in questions if q.expected_quote is not None]
    return MeasuredPipeline(InMemoryRetriever(corpus), FakeGenerator(), settings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else None
    )
    parser.add_argument("--provider", choices=["real", "fake"], required=True)
    parser.add_argument("--questions", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--output", type=Path, help="also write the markdown here")
    args = parser.parse_args(argv)

    os.environ["RAG_PROVIDER"] = args.provider
    try:
        settings = RagSettings.from_env()
    except ValidationError as error:
        print(
            f"cannot start the measurement: {_missing_settings(error)}", file=sys.stderr
        )
        return 2
    questions = load_questions(args.questions)
    try:
        pipeline = (
            _build_real(settings)
            if args.provider == "real"
            else _build_fake(settings, questions)
        )
        report = run(questions, pipeline, args.provider)
    except RuntimeError as error:
        print(f"cannot start the measurement: {error}", file=sys.stderr)
        return 2
    except (ProviderError, RetrievalUnavailable) as error:
        print(f"the measurement stopped: {type(error).__name__}", file=sys.stderr)
        return 1
    markdown = render_markdown(report)
    print(markdown)
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
