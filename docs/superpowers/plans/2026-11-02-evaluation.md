# Evaluation Sub-project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `explorai_eval` package that measures RAG quality with Ragas and Langfuse tracing, establishes a committed baseline, and adds a structural CI job.

**Architecture:** A new `evaluation/` workspace package wraps `MeasuredPipeline` from `ai/measure.py` to collect `EvalSample` records (question, answer, contexts, latency, tokens). `metrics.py` computes the three existing metrics plus four Ragas metrics (LLM-as-judge via Claude). `langfuse_client.py` sends traces and scores to a Langfuse Compose service; it is a no-op when `--langfuse` is absent. CI runs only the fake-provider structural check.

**Tech Stack:** Python 3.12, `uv` workspace, `ragas>=0.2`, `langfuse>=2`, `langchain-anthropic`, `datasets` (Ragas dependency), `pytest`, existing `explorai_ai` and `explorai_db`.

**Spec:** `docs/superpowers/specs/2026-11-02-evaluation-design.md`

## Global Constraints

- Package name: `explorai-eval`; import root: `explorai_eval`.
- `evaluation/` follows the same layout as `backend/`, `ai/`, `pipelines/`: `src/explorai_eval/`, `tests/unit/`, `tests/integration/`.
- `ai/` gains one backward-compatible change only (Task 3); no other `ai/` files are touched.
- All integration tests are marked `@pytest.mark.integration`; CI never runs them.
- Exit codes: 0 success, 1 provider error mid-run, 2 configuration/setup error — identical to `ai/measure.py`.
- Fixture path constant points to `ai/tests/fixtures/questions/questions.json` (shared fixture, not moved).
- `--langfuse` flag is required to enable tracing; `LangfuseClient` is a no-op without it.
- Pin `ragas`, `langfuse`, and `langchain-anthropic` to exact minor versions in `evaluation/pyproject.toml`.
- Coverage gate: 80% (add `--cov-fail-under=80` in `evaluation/pyproject.toml` addopts).

## Review Focus

- **Questions without `expected_answer`**: Ragas metrics must be skipped for them, not error.
- **`--provider fake` with `--langfuse`**: the Langfuse no-op must survive even when `--langfuse` is passed but `LANGFUSE_HOST` is empty.
- **All-unanswerable run**: `retrieval_hit_rate` is `None` (division by zero guard); report must print `n/a`.
- **Ragas returns `NaN`**: individual metric scores can be `float("nan")`; report must handle them (treat as `None`/`n/a`).
- **Token passthrough for refusals**: `AnswerResult.usage` exists even on `insufficient_evidence`; runner must record it.

---

## Task 1: Workspace setup, package scaffold, and questions extension

**Files:**
- Modify: `pyproject.toml` (root)
- Create: `evaluation/pyproject.toml`
- Create: `evaluation/src/explorai_eval/__init__.py`
- Create: `evaluation/src/explorai_eval/py.typed`
- Modify: `ai/tests/fixtures/questions/questions.json`

**Interfaces:**
- Produces: importable `explorai_eval` package; extended question schema with optional `expected_answer`.

- [ ] **Step 1: Add `"evaluation"` to the workspace members in root `pyproject.toml`**

  In `[tool.uv.workspace]`, change `members = ["backend", "db", "pipelines", "ai"]` to include `"evaluation"`.

- [ ] **Step 2: Create `evaluation/pyproject.toml`**

  Package `explorai-eval`, Python `>=3.12`. Dependencies: `explorai-ai`, `ragas>=0.2,<0.3`, `langfuse>=2,<3`, `langchain-anthropic>=0.3,<0.4`, `langchain>=0.3,<0.4`. Dev dependencies: `pytest`, `pytest-cov`. Addopts: `--cov=explorai_eval --cov-fail-under=80`. Test paths: `tests`.

- [ ] **Step 3: Create `evaluation/src/explorai_eval/__init__.py`** (empty) and `py.typed` (empty marker).

- [ ] **Step 4: Extend `ai/tests/fixtures/questions/questions.json`**

  Add `"expected_answer"` to the 10 answerable questions. Concise factual answers matching their `expected_quote`:

  | # | `expected_answer` |
  |---|---|
  | 1 | `"1.18 percentage points"` |
  | 2 | `"Each selected expert executes one forward pass, preserving the active-expert count, expert FLOPs, and all-to-all communication volume."` |
  | 3 | `"independently initialized sigmoid routing heads produce scores that are summed before one top-k selection"` |
  | 4 | `"Routing scores determine which experts process each token and how their outputs are combined."` |
  | 5 | `"8,735 hypotheses over the clinical timelines of vaccine adverse-event reports"` |
  | 6 | `"Art (articulate, retrieve, test), a staged paradigm that exposes each decision as an inspectable output"` |
  | 7 | `"systems fail mostly at choosing which records count"` |
  | 8 | `"Safety alignment is known to be shallow: an aligned model departs from its base almost exclusively over the first few output tokens"` |
  | 9 | `"Multi-turn dialogue, however, remains far less protected."` |
  | 10 | `"once a response opens compliantly the aligned distribution collapses back to the base model"` |

  The 4 unanswerable questions (`paper: "none"`) get no `expected_answer` field.

- [ ] **Step 5: Run `uv sync` and verify the workspace resolves**

  ```bash
  uv sync
  ```
  Expected: no resolution error; `explorai-eval` appears in the lock file.

- [ ] **Step 6: Commit**

  ```bash
  git add pyproject.toml evaluation/ ai/tests/fixtures/questions/questions.json
  git commit -m "feat(eval): scaffold evaluation package and extend question set"
  ```

---

## Task 2: EvalSettings and dataset loader

**Files:**
- Create: `evaluation/src/explorai_eval/config.py`
- Create: `evaluation/src/explorai_eval/dataset.py`
- Create: `evaluation/tests/__init__.py`, `evaluation/tests/unit/__init__.py`
- Create: `evaluation/tests/unit/test_dataset.py`

**Interfaces:**
- Consumes: `explorai_ai.config.RagSettings`
- Produces:
  - `EvalSettings` (extends `RagSettings`): fields `langfuse_host: str = ""`, `langfuse_public_key: str = ""`, `langfuse_secret_key: SecretStr | None = None`; class method `EvalSettings.from_env() -> EvalSettings`.
  - `Question(question: str, paper: str, expected_quote: str | None, expected_answer: str | None)`
  - `load_questions(path: Path = FIXTURE_PATH) -> list[Question]` — raises `ValueError` on bad schema.
  - `FIXTURE_PATH: Path` constant pointing at `ai/tests/fixtures/questions/questions.json`.

- [ ] **Step 1: Write failing tests in `evaluation/tests/unit/test_dataset.py`**

  ```python
  def test_load_questions_returns_14():
      questions = load_questions()
      assert len(questions) == 14


  def test_answerable_have_expected_quote():
      for q in load_questions():
          if q.paper != "none":
              assert q.expected_quote is not None


  def test_unanswerable_have_no_expected_answer():
      for q in load_questions():
          if q.paper == "none":
              assert q.expected_answer is None


  def test_answerable_have_expected_answer():
      answerable = [q for q in load_questions() if q.paper != "none"]
      assert all(q.expected_answer is not None for q in answerable)


  def test_load_questions_missing_key_raises():
      with pytest.raises(ValueError):
          load_questions(path_to_json_missing_question_key)
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest evaluation/tests/unit/test_dataset.py -v --no-cov
  ```
  Expected: FAIL (ImportError or AttributeError).

- [ ] **Step 3: Implement `evaluation/src/explorai_eval/config.py`**

  `EvalSettings` extends `RagSettings`; uses `model_config = SettingsConfigDict(env_prefix="", extra="ignore")` (same as `RagSettings`); the three Langfuse fields are optional with empty-string defaults so the class instantiates without any Langfuse env vars.

- [ ] **Step 4: Implement `evaluation/src/explorai_eval/dataset.py`**

  `FIXTURE_PATH` is resolved relative to `__file__` (four parents up, then `ai/tests/fixtures/questions/questions.json`). `load_questions` reads and validates: required keys are `"question"`, `"paper"`, `"expected_quote"`; `"expected_answer"` is optional (defaults to `None`). Raises `ValueError` listing the offending entry if a required key is absent.

- [ ] **Step 5: Run tests to verify they pass**

  ```bash
  uv run pytest evaluation/tests/unit/test_dataset.py -v --no-cov
  ```
  Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

  ```bash
  git add evaluation/src/explorai_eval/config.py evaluation/src/explorai_eval/dataset.py \
          evaluation/tests/ 
  git commit -m "feat(eval): add EvalSettings and dataset loader"
  ```

---

## Task 3: Token passthrough in `ai/measure.py` and EvalSample runner

**Files:**
- Modify: `ai/src/explorai_ai/measure.py` (extend `Outcome`, update `MeasuredPipeline.ask`)
- Create: `evaluation/src/explorai_eval/runner.py`
- Create: `evaluation/tests/unit/test_runner.py`

**Interfaces:**
- Consumes: `MeasuredPipeline` from `ai.measure`; `Question` from `explorai_eval.dataset`; `Usage` from `explorai_ai.models`.
- Produces:
  - `Outcome` in `ai/measure.py` gains `usage: Usage` (default `Usage(0, 0)`).
  - `EvalSample(question: str, answer: str, status: str, retrieved_contexts: list[str], expected_quote: str | None, expected_answer: str | None, latency_s: float, input_tokens: int, output_tokens: int)`.
  - `run_evaluation(questions: Sequence[Question], pipeline: MeasuredPipeline) -> list[EvalSample]` — one sample per question, latency measured with `time.perf_counter()`.

- [ ] **Step 1: Write failing test for `Outcome.usage` in `ai/tests/unit/test_measure.py`**

  Add to the existing test file:
  ```python
  def test_outcome_has_usage_field():
      outcome = Outcome(status="answered", kept_texts=[], usage=Usage(10, 5))
      assert outcome.usage.input_tokens == 10
      assert outcome.usage.output_tokens == 5
  ```

- [ ] **Step 2: Run to verify it fails**

  ```bash
  uv run pytest ai/tests/unit/test_measure.py::test_outcome_has_usage_field --no-cov
  ```
  Expected: FAIL.

- [ ] **Step 3: Extend `Outcome` in `ai/src/explorai_ai/measure.py`**

  Add `usage: Usage = dataclasses.field(default_factory=lambda: Usage(0, 0))` (import `Usage` from `explorai_ai.models`). In `MeasuredPipeline.ask()`, populate `usage=result.usage` from the `AnswerResult`.

- [ ] **Step 4: Run existing `ai` measure tests to verify nothing regressed**

  ```bash
  uv run pytest ai/tests/unit/test_measure.py --no-cov
  ```
  Expected: all pass.

- [ ] **Step 5: Write failing tests for `run_evaluation` in `evaluation/tests/unit/test_runner.py`**

  ```python
  def test_run_evaluation_returns_one_sample_per_question(fake_pipeline, questions):
      samples = run_evaluation(questions, fake_pipeline)
      assert len(samples) == len(questions)


  def test_sample_latency_is_positive(fake_pipeline, questions):
      samples = run_evaluation(questions[:1], fake_pipeline)
      assert samples[0].latency_s > 0


  def test_sample_preserves_expected_fields(fake_pipeline, questions):
      samples = run_evaluation(questions[:1], fake_pipeline)
      assert samples[0].question == questions[0].question
      assert samples[0].expected_quote == questions[0].expected_quote
      assert samples[0].expected_answer == questions[0].expected_answer


  def test_sample_token_counts_are_non_negative(fake_pipeline, questions):
      samples = run_evaluation(questions[:1], fake_pipeline)
      assert samples[0].input_tokens >= 0
      assert samples[0].output_tokens >= 0
  ```

  `fake_pipeline` fixture: a minimal object with `.ask(question) -> Outcome` returning `Outcome("answered", ["some text"], Usage(1, 1))`.

- [ ] **Step 6: Run tests to verify they fail**

  ```bash
  uv run pytest evaluation/tests/unit/test_runner.py -v --no-cov
  ```
  Expected: FAIL.

- [ ] **Step 7: Implement `evaluation/src/explorai_eval/runner.py`**

  `run_evaluation` iterates questions, calls `time.perf_counter()` before and after `pipeline.ask(q.question)`, builds `EvalSample` from the returned `Outcome` plus elapsed time and `outcome.usage`.

- [ ] **Step 8: Run tests to verify they pass**

  ```bash
  uv run pytest evaluation/tests/unit/test_runner.py -v --no-cov
  ```
  Expected: PASS.

- [ ] **Step 9: Commit**

  ```bash
  git add ai/src/explorai_ai/measure.py ai/tests/unit/test_measure.py \
          evaluation/src/explorai_eval/runner.py evaluation/tests/unit/test_runner.py
  git commit -m "feat(eval): add EvalSample runner and expose usage in Outcome"
  ```

---

## Task 4: Existing metrics (hit rate, refusals)

**Files:**
- Create: `evaluation/src/explorai_eval/metrics.py`
- Create: `evaluation/tests/unit/test_metrics.py`

**Interfaces:**
- Consumes: `EvalSample` from `explorai_eval.runner`; `normalise_for_match` from `explorai_ai.citations`.
- Produces:
  - `ExistingMetrics(retrieval_hit_rate: float | None, correct_refusal_rate: float | None, false_refusal_rate: float | None, retrieval_hits: int, n_answerable: int, correct_refusals: int, n_unanswerable: int, false_refusals: int, missed_questions: list[str])`.
  - `compute_existing_metrics(samples: Sequence[EvalSample]) -> ExistingMetrics` — identical logic to `ai/measure.py`'s `score_retrieval` and `score_refusal`.

- [ ] **Step 1: Write failing tests**

  ```python
  def test_perfect_hit_rate(perfect_samples):
      result = compute_existing_metrics(perfect_samples)
      assert result.retrieval_hit_rate == 1.0


  def test_zero_hit_rate(missed_samples):
      result = compute_existing_metrics(missed_samples)
      assert result.retrieval_hit_rate == 0.0


  def test_hit_rate_none_when_no_answerable(unanswerable_only_samples):
      result = compute_existing_metrics(unanswerable_only_samples)
      assert result.retrieval_hit_rate is None


  def test_correct_refusal_rate(refusal_samples):
      result = compute_existing_metrics(refusal_samples)
      assert result.correct_refusal_rate == 1.0


  def test_false_refusals_counted(false_refusal_samples):
      result = compute_existing_metrics(false_refusal_samples)
      assert result.false_refusals == 1
  ```

- [ ] **Step 2: Run to verify they fail**

  ```bash
  uv run pytest evaluation/tests/unit/test_metrics.py -v --no-cov
  ```

- [ ] **Step 3: Implement `compute_existing_metrics` in `evaluation/src/explorai_eval/metrics.py`**

  Port the logic from `ai/measure.py` (`score_retrieval`, `score_refusal`). A sample is unanswerable when `expected_quote is None`. Hit: normalised quote in any retrieved context. Refusal correct: status `"insufficient_evidence"` iff `expected_quote is None`.

- [ ] **Step 4: Run to verify they pass**

  ```bash
  uv run pytest evaluation/tests/unit/test_metrics.py -v --no-cov
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add evaluation/src/explorai_eval/metrics.py evaluation/tests/unit/test_metrics.py
  git commit -m "feat(eval): add existing metrics (hit rate, refusals)"
  ```

---

## Task 5: Ragas metrics

**Files:**
- Modify: `evaluation/src/explorai_eval/metrics.py`
- Modify: `evaluation/tests/unit/test_metrics.py`

**Interfaces:**
- Consumes: `EvalSample`; `EvalSettings` (for Anthropic key to configure Ragas LLM); Ragas `evaluate`, `context_recall`, `faithfulness`, `answer_correctness`, `answer_relevancy`.
- Produces:
  - `RagasMetrics(context_recall: float | None, faithfulness: float | None, answer_correctness: float | None, answer_relevancy: float | None)` — each field is `None` when the metric returned `NaN` or no scorable samples existed.
  - `compute_ragas_metrics(samples: Sequence[EvalSample], settings: EvalSettings) -> RagasMetrics` — builds a Ragas `EvaluationDataset` from samples that have `expected_answer`; returns `RagasMetrics(None, None, None, None)` when no such samples exist.

- [ ] **Step 1: Write failing tests for `RagasMetrics` and `NaN` guard**

  ```python
  def test_ragas_metrics_none_when_no_expected_answer(
      samples_without_expected_answer, fake_settings
  ):
      result = compute_ragas_metrics(samples_without_expected_answer, fake_settings)
      assert result.context_recall is None
      assert result.faithfulness is None
      assert result.answer_correctness is None
      assert result.answer_relevancy is None


  def test_ragas_metrics_nan_becomes_none(nan_ragas_result, fake_settings):
      # nan_ragas_result fixture monkeypatches ragas.evaluate to return NaN scores
      result = compute_ragas_metrics([one_sample_with_answer], fake_settings)
      assert result.context_recall is None
  ```

- [ ] **Step 2: Run to verify they fail**

  ```bash
  uv run pytest evaluation/tests/unit/test_metrics.py::test_ragas_metrics_none_when_no_expected_answer \
               evaluation/tests/unit/test_metrics.py::test_ragas_metrics_nan_becomes_none -v --no-cov
  ```

- [ ] **Step 3: Implement Ragas integration in `evaluation/src/explorai_eval/metrics.py`**

  `compute_ragas_metrics` filters samples to those with `expected_answer is not None`. Returns all-`None` `RagasMetrics` immediately if the filtered list is empty. Otherwise builds a `ragas.EvaluationDataset` (or `datasets.Dataset` depending on the Ragas version) with columns `question`, `answer`, `contexts` (list of strings), `ground_truth` (`expected_answer`). Calls `ragas.evaluate(dataset, metrics=[context_recall, faithfulness, answer_correctness, answer_relevancy], llm=<claude_llm>, embeddings=<voyage_embeddings>)`. Configure the custom LLM via `ragas.llms.LangchainLLMWrapper(ChatAnthropic(model=settings.generation_model, api_key=settings.anthropic_api_key))` and embeddings via `ragas.embeddings.LangchainEmbeddingsWrapper(VoyageAIEmbeddings(...))`. Each metric score: convert `float("nan")` to `None`.

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest evaluation/tests/unit/test_metrics.py -v --no-cov
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add evaluation/src/explorai_eval/metrics.py evaluation/tests/unit/test_metrics.py
  git commit -m "feat(eval): add Ragas metrics (context_recall, faithfulness, answer_correctness, answer_relevancy)"
  ```

---

## Task 6: Langfuse client

**Files:**
- Create: `evaluation/src/explorai_eval/langfuse_client.py`
- Create: `evaluation/tests/unit/test_langfuse_client.py`

**Interfaces:**
- Consumes: `EvalSample`; `EvalSettings`.
- Produces:
  - `LangfuseClient(settings: EvalSettings, dataset_name: str, run_name: str, *, enabled: bool)`.
  - `.record_sample(sample: EvalSample, scores: dict[str, float]) -> None` — sends one trace with a retrieval span, a generation span, and one score per metric.
  - `.flush() -> None` — flushes the Langfuse SDK buffer.
  - `.enabled: bool` property — `True` only when `enabled=True` AND `settings.langfuse_host` is non-empty.

- [ ] **Step 1: Write failing tests**

  ```python
  def test_disabled_when_host_empty():
      settings = EvalSettings(langfuse_host="")
      client = LangfuseClient(settings, "eval", "run-001", enabled=True)
      assert not client.enabled

  def test_disabled_when_flag_false():
      settings = EvalSettings(langfuse_host="http://127.0.0.1:3000", ...)
      client = LangfuseClient(settings, "eval", "run-001", enabled=False)
      assert not client.enabled

  def test_record_sample_noop_when_disabled(sample):
      # Verify no Langfuse SDK calls are made when disabled
      client = LangfuseClient(EvalSettings(langfuse_host=""), "eval", "r", enabled=True)
      client.record_sample(sample, {"faithfulness": 0.9})  # must not raise

  def test_flush_noop_when_disabled():
      client = LangfuseClient(EvalSettings(langfuse_host=""), "eval", "r", enabled=True)
      client.flush()  # must not raise
  ```

- [ ] **Step 2: Run to verify they fail**

  ```bash
  uv run pytest evaluation/tests/unit/test_langfuse_client.py -v --no-cov
  ```

- [ ] **Step 3: Implement `evaluation/src/explorai_eval/langfuse_client.py`**

  `__init__` computes `self._enabled = enabled and bool(settings.langfuse_host)`. When disabled, all methods are no-ops. When enabled: initialise `langfuse.Langfuse(host=..., public_key=..., secret_key=...)` in `__init__`. In `record_sample`: create a trace, add a span named `"retrieval"` with `output=sample.retrieved_contexts`, add a generation named `"generate"` with `output=sample.answer` and `usage={"input": sample.input_tokens, "output": sample.output_tokens}`, add one score per key in `scores`. In `flush`: call `self._lf.flush()`.

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest evaluation/tests/unit/test_langfuse_client.py -v --no-cov
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add evaluation/src/explorai_eval/langfuse_client.py evaluation/tests/unit/test_langfuse_client.py
  git commit -m "feat(eval): add LangfuseClient with no-op mode"
  ```

---

## Task 7: Report renderer

**Files:**
- Create: `evaluation/src/explorai_eval/report.py`
- Create: `evaluation/tests/unit/test_report.py`

**Interfaces:**
- Consumes: `ExistingMetrics`; `RagasMetrics`; provider name and run totals.
- Produces:
  - `render_markdown(existing: ExistingMetrics, ragas: RagasMetrics | None, *, provider: str, total_latency_s: float, total_input_tokens: int, total_output_tokens: int) -> str`.

- [ ] **Step 1: Write failing tests**

  ```python
  def test_render_includes_provider_name():
      md = render_markdown(existing_metrics, None, provider="fake", ...)
      assert "fake" in md

  def test_render_none_rate_as_na():
      existing = ExistingMetrics(retrieval_hit_rate=None, ...)
      md = render_markdown(existing, None, provider="fake", ...)
      assert "n/a" in md

  def test_render_ragas_section_absent_when_none():
      md = render_markdown(existing_metrics, None, provider="fake", ...)
      assert "context_recall" not in md

  def test_render_ragas_section_present():
      md = render_markdown(existing_metrics, ragas_metrics, provider="real", ...)
      assert "context_recall" in md
      assert "faithfulness" in md

  def test_render_nan_ragas_as_na():
      ragas = RagasMetrics(context_recall=None, faithfulness=None, ...)
      md = render_markdown(existing_metrics, ragas, provider="real", ...)
      assert "n/a" in md

  def test_render_latency_appears():
      md = render_markdown(existing_metrics, None, provider="fake",
                           total_latency_s=12.5, total_input_tokens=100, total_output_tokens=50)
      assert "12.5" in md or "12" in md
  ```

- [ ] **Step 2: Run to verify they fail**

  ```bash
  uv run pytest evaluation/tests/unit/test_report.py -v --no-cov
  ```

- [ ] **Step 3: Implement `evaluation/src/explorai_eval/report.py`**

  `render_markdown` returns a Markdown string. Sections: provider header, fake-provider disclaimer (when `provider == "fake"`), existing-metrics table, optional Ragas table (only when `ragas is not None`), performance summary (latency and tokens). Use `"n/a"` for `None` and `float("nan")` metric values. Format `retrieval_hit_rate` and Ragas scores as percentages (`:.0%`).

- [ ] **Step 4: Run to verify they pass**

  ```bash
  uv run pytest evaluation/tests/unit/test_report.py -v --no-cov
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add evaluation/src/explorai_eval/report.py evaluation/tests/unit/test_report.py
  git commit -m "feat(eval): add markdown report renderer"
  ```

---

## Task 8: CLI entry point

**Files:**
- Create: `evaluation/src/explorai_eval/measure.py`
- Create: `evaluation/tests/unit/test_measure_cli.py`

**Interfaces:**
- Consumes: all `explorai_eval` modules; `MeasuredPipeline`, `InMemoryRetriever`, `FakeGenerator` from `ai.measure`; `FakeEmbedder`, `FakeReranker` from `ai.fakes`.
- Produces: `main(argv: Sequence[str] | None = None) -> int`; module is runnable as `python -m explorai_eval.measure`.

- [ ] **Step 1: Write failing tests for the fake-provider path**

  ```python
  def test_fake_provider_exits_zero():
      assert main(["--provider", "fake"]) == 0


  def test_fake_provider_writes_output_file(tmp_path):
      out = tmp_path / "report.md"
      main(["--provider", "fake", "--output", str(out)])
      assert out.exists()
      assert "fake" in out.read_text()


  def test_missing_provider_arg_exits_nonzero():
      with pytest.raises(SystemExit) as exc:
          main([])
      assert exc.value.code != 0
  ```

- [ ] **Step 2: Run to verify they fail**

  ```bash
  uv run pytest evaluation/tests/unit/test_measure_cli.py -v --no-cov
  ```

- [ ] **Step 3: Implement `evaluation/src/explorai_eval/measure.py`**

  `argparse` with `--provider real|fake` (required), `--questions PATH`, `--output PATH`, `--langfuse` (store_true), `--dataset-name NAME` (default `"explorai-eval"`). For `fake`: build `InMemoryRetriever` from expected quotes + `FakeGenerator` + `FakeSettings`; call `run_evaluation`; call `compute_existing_metrics`; pass `ragas=None` (skip Ragas for fake). For `real`: build the real pipeline via `_build_real` (port from `ai/measure.py` or import it); call `compute_existing_metrics` and `compute_ragas_metrics`. Build `LangfuseClient(enabled=args.langfuse, run_name=f"{git_sha}-{timestamp}")` where `git_sha` comes from `subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])` (fallback to `"unknown"`). Call `client.record_sample` for each sample, `client.flush()` before exit. Render and print report. Write to `--output` if given. Exit codes: 2 on config/validation error, 1 on `ProviderError`, 0 on success.

  Add `[project.scripts] explorai-eval = "explorai_eval.measure:main"` to `evaluation/pyproject.toml`.

- [ ] **Step 4: Run to verify tests pass**

  ```bash
  uv run pytest evaluation/tests/unit/test_measure_cli.py -v --no-cov
  ```

- [ ] **Step 5: Smoke-check the CLI manually**

  ```bash
  uv run python -m explorai_eval.measure --provider fake
  ```
  Expected: markdown table printed to stdout, exit 0.

- [ ] **Step 6: Run the full evaluation unit suite**

  ```bash
  uv run pytest evaluation/tests -m "not integration" --no-cov
  ```
  Expected: all pass.

- [ ] **Step 7: Commit**

  ```bash
  git add evaluation/src/explorai_eval/measure.py evaluation/tests/unit/test_measure_cli.py \
          evaluation/pyproject.toml
  git commit -m "feat(eval): add CLI entry point and wire all evaluation modules"
  ```

---

## Task 9: Langfuse Compose service and environment

**Files:**
- Modify: `infra/docker-compose.yml`
- Modify: `infra/.env.example`
- Modify: `docs/evaluation/rag-baseline.md`

**Interfaces:**
- Produces: `langfuse` service reachable at `http://127.0.0.1:3000`; new env vars documented.

- [ ] **Step 1: Add `langfuse` service to `infra/docker-compose.yml`**

  Use image `langfuse/langfuse:3`. Depends on `db`. Ports: `3000:3000`. Environment variables (from `.env`): `DATABASE_URL` pointing to a `langfuse` database on the same PostgreSQL instance, `NEXTAUTH_SECRET`, `SALT`, `NEXTAUTH_URL=http://127.0.0.1:3000`, `TELEMETRY_ENABLED=false`. Add a `langfuse_db` creation step (an `initdb` script or a pre-start command) so PostgreSQL creates the `langfuse` database on first start.

  > Note: Langfuse 3 runs its own migrations on startup; the `DATABASE_URL` must point to a database it can own exclusively. Use `postgresql://explorai:${POSTGRES_PASSWORD}@db:5432/langfuse` (separate database, same PostgreSQL instance).

- [ ] **Step 2: Add Langfuse variables to `infra/.env.example`**

  ```
  LANGFUSE_DATABASE_URL=postgresql://explorai:changeme@127.0.0.1:5432/langfuse
  LANGFUSE_NEXTAUTH_SECRET=changeme-secret-at-least-32-chars
  LANGFUSE_SALT=changeme-salt
  LANGFUSE_PUBLIC_KEY=
  LANGFUSE_SECRET_KEY=
  LANGFUSE_HOST=http://127.0.0.1:3000
  ```

- [ ] **Step 3: Update `docs/evaluation/rag-baseline.md`**

  Replace the "How to run" section with the new CLI (`python -m explorai_eval.measure --provider real --langfuse --output docs/evaluation/rag-baseline-run.md`). Add a prerequisite: start the Langfuse service and create the API key pair from the Langfuse UI before using `--langfuse`. Keep the existing questions table and limits sections unchanged.

- [ ] **Step 4: Commit**

  ```bash
  git add infra/docker-compose.yml infra/.env.example docs/evaluation/rag-baseline.md
  git commit -m "feat(eval): add Langfuse service to Compose and document baseline workflow"
  ```

---

## Task 10: CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `evaluation` CI job that runs unit tests and the fake-provider CLI check, no services, no keys.

- [ ] **Step 1: Add the `evaluation` job to `.github/workflows/ci.yml`**

  New job after the `ai` job. No services block (no PostgreSQL, Redis, Qdrant or Langfuse needed). Steps:
  1. `actions/checkout`
  2. `astral-sh/setup-uv` with the same `UV_VERSION`.
  3. `uv sync`.
  4. Lint: `uv run ruff check evaluation`.
  5. Format check: `uv run ruff format --check evaluation`.
  6. Type check: `uv run mypy --config-file evaluation/pyproject.toml evaluation/src evaluation/tests`.
  7. Unit tests: `uv run pytest evaluation/tests -m "not integration"`.
  8. Plumbing check: `uv run python -m explorai_eval.measure --provider fake`.

  No `env` block needed (no keys, `LANGFUSE_HOST` absent → no-op).

- [ ] **Step 2: Add `mypy` config to `evaluation/pyproject.toml`**

  `[tool.mypy]` section: `strict = true`, `python_version = "3.12"`, `ignore_missing_imports = true` (for Ragas and Langfuse which may lack stubs).

- [ ] **Step 3: Run the CI checks locally**

  ```bash
  uv run ruff check evaluation
  uv run ruff format --check evaluation
  uv run mypy --config-file evaluation/pyproject.toml evaluation/src evaluation/tests
  uv run pytest evaluation/tests -m "not integration"
  uv run python -m explorai_eval.measure --provider fake
  ```
  Expected: all pass.

- [ ] **Step 4: Commit**

  ```bash
  git add .github/workflows/ci.yml evaluation/pyproject.toml
  git commit -m "ci: add evaluation job (structural checks, no keys)"
  ```
