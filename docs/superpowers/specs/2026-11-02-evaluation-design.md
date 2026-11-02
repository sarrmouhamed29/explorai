# Sub-project 4: Evaluation, design spec

Part of the master plan (`plan.md`). Date: 2026-11-02. Status: approved.

## 1. Goal

Measure the quality of the RAG pipeline on a hand-annotated question set and give every future change a number to beat. The success criteria from `plan.md`: every RAG change is compared with a baseline using numbers.

## 2. Scope

**In scope**
- A new `evaluation/` workspace package `explorai_eval`.
- Extension of the existing question set with `expected_answer` fields.
- Four Ragas metrics (context recall, faithfulness, answer correctness, answer relevancy) plus the three existing metrics from `ai/measure.py` (retrieval hit rate, correct refusal rate, false refusals).
- Langfuse tracing of evaluation runs (one trace per question, one dataset run per evaluation run).
- A `langfuse` service in Compose.
- A CLI entry point `python -m explorai_eval.measure` that replaces `ai/measure.py` for real-provider runs.
- Structural CI checks (fake provider, no keys, no cost).
- A committed baseline file in `docs/evaluation/` that serves as a manual regression gate.

**Out of scope**
- Langfuse instrumentation inside `ai/` or `backend/` (sub-project 10: Production).
- Automated CI regression gate on real scores (costs money; manual gate in PR review instead).
- Prompt injection and adversarial tests: sub-project 5.
- Frontend for evaluation results: sub-project 6.
- Fine-tuning, model routing, caching comparisons: sub-project 11.
- Streaming answers, conversation history.

## 3. Decisions and rejected alternatives

| Topic | Decision | Alternatives not chosen |
|---|---|---|
| Evaluation framework | Ragas: built for RAG, standard industry metrics, integrates with Langfuse | DeepEval (more flexible/pytest-like but less RAG-idiomatic); rolling our own scorers (avoids a dependency but duplicates work that Ragas does well) |
| LLM-as-judge | Claude via Anthropic API (via Ragas's custom LLM interface) | GPT-4 (Ragas default, would add a second paid provider); no LLM judge (removes answer quality metrics) |
| Langfuse placement | Only in `evaluation/`; `ai/` unchanged | Deep instrumentation in `ai/` (cleaner traces but `ai/` takes a Langfuse dependency and overhead is always on); Langfuse deferred to sub-project 10 (loses tracing for the evaluation sub-project entirely) |
| CI strategy | Structural checks with fake provider only; real evaluation on-demand by the maintainer | Full real evaluation in CI (costs money on every PR, needs API keys in GitHub Secrets); scheduled CI run (better than per-PR but still costs money and needs keys) |
| Regression gate | Manual: the maintainer runs `explorai_eval.measure --provider real`, commits the markdown to `docs/evaluation/`, and PR review compares new runs to it | Automated gate in CI (costs money); no gate (there is then no regression protection) |
| Question set | Extend existing `questions.json` with `expected_answer`; keep it in `ai/tests/fixtures/` as a shared fixture | Move the file to `evaluation/` (would break `ai/measure.py`'s path and make `ai/` depend on `evaluation/`); a separate file (duplication) |

## 4. Package structure

```
evaluation/
  pyproject.toml              # package explorai_eval; depends on explorai_ai, explorai_db, ragas, langfuse
  src/explorai_eval/
    __init__.py
    config.py                 # EvalSettings: RagSettings + Langfuse coords
    dataset.py                # loads and validates questions.json; Question dataclass with expected_answer
    runner.py                 # wraps AnswerPipeline; collects (question, answer, contexts, latency, tokens)
    metrics.py                # computes Ragas metrics + the three existing metrics
    langfuse_client.py        # sends traces, generations and scores to Langfuse; no-op when host absent
    report.py                 # renders the markdown report
    measure.py                # CLI entry point
  tests/
    unit/
      test_dataset.py         # question loading and validation
      test_metrics.py         # scorer logic with fake inputs
      test_report.py          # markdown rendering
    integration/              # need DATABASE_URL, QDRANT_URL, real keys; marked integration
      test_runner.py
```

`ai/measure.py` is kept for the fake/plumbing check (`--provider fake`). The real measurement moves to `explorai_eval.measure`.

## 5. Question set extension

The existing `ai/tests/fixtures/questions/questions.json` gains an optional `expected_answer` field:

```json
{
  "question": "By how many percentage points does full Multi-View MoE improve the mean score?",
  "paper": "2610.40577",
  "expected_quote": "full Multi-View MoE improves the mean score across nine downstream tasks by 1.18 percentage points over the baseline",
  "expected_answer": "1.18 percentage points"
}
```

- `expected_answer` is absent for unanswerable questions (`paper: "none"`).
- Questions without `expected_answer` are scored only on retrieval hit rate and refusal; Ragas metrics are skipped for them.
- The existing `ai/measure.py` ignores the new field (backward compatible).
- The maintainer must validate every `expected_answer` before the first real run (the same review obligation as for `expected_quote`).

## 6. Metrics

### Existing metrics (no LLM cost)

| Metric | Over | Definition |
|---|---|---|
| `retrieval_hit_rate` | answerable questions | the normalised expected quote occurs in at least one of the kept chunks shown to the generator |
| `correct_refusal_rate` | unanswerable questions | the pipeline returns `insufficient_evidence` |
| `false_refusals` | answerable questions | questions refused although they should have been answered (count and rate) |

These are computed by `explorai_eval.metrics` using the same logic as `ai/measure.py` (`normalise_for_match`).

### Ragas metrics (LLM-as-judge; only for answerable questions with `expected_answer`)

| Ragas metric | What it measures |
|---|---|
| `context_recall` | Do the retrieved chunks cover the expected answer? |
| `faithfulness` | Is every claim in the generated answer supported by the retrieved chunks? |
| `answer_correctness` | How close is the generated answer to the expected answer factually? |
| `answer_relevancy` | Does the generated answer actually address the question asked? |

**LLM-as-judge configuration.** Ragas accepts a custom LLM and embedder. We point both to Claude and Voyage respectively via Ragas's `LangchainLLMWrapper` / `LangchainEmbeddingsWrapper`. This keeps the project on a single pair of providers and avoids introducing an OpenAI dependency.

**Cost.** Each Ragas metric call sends the question + contexts + answer to Claude. For 10 answerable questions and 4 metrics, this is roughly 40 judge calls per run. At current Claude pricing this is a few cents per full evaluation run.

### Latency and cost

Collected by the runner without an external service:
- Latency: `time.perf_counter()` around each `pipeline.ask()` call.
- Token cost: extracted from the Anthropic response's `usage` field (passed back through `AnswerResult`; if not yet exposed, a small change to `AnswerPipeline` adds it).
- Reported as totals and per-question averages in the markdown report.

## 7. Runner

`runner.py` collects one `EvalSample` per question:

```python
@dataclass
class EvalSample:
    question: str
    answer: str  # generated answer or refusal message
    status: str  # "answered" | "insufficient_evidence" | ...
    retrieved_contexts: list[str]  # texts of the kept chunks
    expected_quote: str | None
    expected_answer: str | None
    latency_s: float
    input_tokens: int
    output_tokens: int
```

The runner wraps `MeasuredPipeline` (from `ai/measure.py`) to intercept the kept chunks, identical to how the existing measurement works. `ai/` is not modified.

## 8. Langfuse integration

### Compose service

Added to `infra/docker-compose.yml`:

```yaml
langfuse:
  image: langfuse/langfuse:3
  depends_on: [db]
  ports: ["3000:3000"]
  environment:
    DATABASE_URL: ${LANGFUSE_DATABASE_URL}
    NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET}
    SALT: ${LANGFUSE_SALT}
    NEXTAUTH_URL: http://127.0.0.1:3000
```

Langfuse uses a separate PostgreSQL database (`langfuse_db`) on the same PostgreSQL instance to avoid schema conflicts. New variables added to `.env.example`.

### SDK usage in `langfuse_client.py`

`LangfuseClient` is a thin wrapper with a no-op mode when `LANGFUSE_HOST` is absent (for CI and for runs without `--langfuse`):

```python
class LangfuseClient:
    def record_trace(self, sample: EvalSample, scores: dict[str, float]) -> None: ...
    def flush(self) -> None: ...
```

Per trace:
- One `langfuse.trace(name="eval-question", input=question)`.
- One `span` for retrieval (output: retrieved_contexts).
- One `generation` for the answer (output: answer, usage: tokens).
- One `score` per Ragas metric.

Per evaluation run:
- One *dataset* (named `explorai-eval`, created once, idempotent).
- One *dataset run* named `{commit_sha[:8]}-{iso_timestamp}` to allow side-by-side comparison in the Langfuse UI.

### CLI flag

`--langfuse` enables tracing. Without the flag, `LangfuseClient` is a no-op even if `LANGFUSE_HOST` is set. This keeps test runs clean and makes tracing an explicit opt-in.

## 9. CLI

```
uv run python -m explorai_eval.measure
  --provider real|fake       (required)
  --questions PATH           (default: ai/tests/fixtures/questions/questions.json)
  --output PATH              (write markdown report here too)
  --langfuse                 (enable Langfuse tracing)
  --dataset-name NAME        (Langfuse dataset name; default: "explorai-eval")
```

Exit codes match `ai/measure.py`: 0 success, 1 provider error mid-run, 2 configuration error.

`--provider fake` runs the existing plumbing check (in-memory corpus, fake providers, Ragas receives dummy inputs, scores are meaningless). It is the CI-safe mode.

## 10. CI

The `evaluation` CI job (added to `.github/workflows/ci.yml`):

```yaml
- name: Unit tests
  run: uv run pytest evaluation/tests -m "not integration" --no-cov

- name: Structural plumbing check
  run: uv run python -m explorai_eval.measure --provider fake
```

No API keys required. No cost. No Langfuse host (no-op).

The integration tests (`-m integration`) require `DATABASE_URL`, `QDRANT_URL`, real keys and the Langfuse service. They do not run in CI; the maintainer runs them locally after the first full stack deployment.

## 11. Baseline workflow

1. Ingest and index the three annotated papers (`index-pending` deployment).
2. Validate the `expected_answer` fields in `questions.json`.
3. Run:
   ```bash
   uv run python -m explorai_eval.measure \
     --provider real \
     --langfuse \
     --output docs/evaluation/rag-baseline-run.md
   ```
4. Commit `docs/evaluation/rag-baseline-run.md` as the reference baseline.
5. For every subsequent change: run the same command, compare the new output to the committed baseline. A degradation of more than 5 percentage points on any metric blocks the PR.

The threshold (5 pp) is a starting point; adjust after the first real run when the absolute values are known.

## 12. Evaluation criteria

- Structural CI passes (fake provider, no keys).
- `explorai_eval.measure --provider fake` exits 0.
- The four Ragas metrics are computed and appear in the markdown report for answerable questions with `expected_answer`.
- Langfuse receives traces when `--langfuse` is passed; the UI shows the dataset run.
- A committed `docs/evaluation/rag-baseline-run.md` exists with real numbers.
- Latency and token cost appear in the report.

## 13. Threats and guardrails

| Threat | Guardrail |
|---|---|
| Ragas LLM judge costs spiral | Each full run is an explicit CLI call; CI only runs the fake mode. Cost per run is estimated and documented. |
| Non-determinism in LLM-as-judge | Run `--provider real` more than once before treating a result as the baseline; report the date, models and commit. |
| `expected_answer` written by the same assistant that built the pipeline (optimism bias) | Maintainer validates every answer before the first real run; document the validation step. |
| Langfuse service unavailable | `LangfuseClient` is always a no-op without `--langfuse`; evaluation runs with `--provider fake` never need it. |
| Question set too small (10 answerable) | Documented in `rag-baseline.md`; one question moves a rate by 10 pp. Extend the set after the first real run. |
| Ragas API changes | Pin the Ragas version in `evaluation/pyproject.toml`; update only when the CI is green. |
