"""Langfuse client with no-op mode when tracing is disabled."""

from explorai_eval.config import EvalSettings
from explorai_eval.runner import EvalSample


class LangfuseClient:
    """Send evaluation traces and scores to Langfuse.

    When ``enabled`` is False or ``settings.langfuse_host`` is empty, all
    methods are no-ops and no network calls are made.
    """

    def __init__(
        self,
        settings: EvalSettings,
        dataset_name: str,
        run_name: str,
        *,
        enabled: bool,
    ) -> None:
        self._enabled = enabled and bool(settings.langfuse_host)
        self._dataset_name = dataset_name
        self._run_name = run_name
        self._lf = None

        if self._enabled:
            import langfuse

            secret = settings.langfuse_secret_key
            self._lf = langfuse.Langfuse(
                host=settings.langfuse_host,
                public_key=settings.langfuse_public_key,
                secret_key=secret.get_secret_value() if secret else "",
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record_sample(self, sample: EvalSample, scores: dict[str, float]) -> None:
        if not self._enabled or self._lf is None:
            return

        trace = self._lf.trace(
            name=sample.question,
            input=sample.question,
            output=sample.answer,
            metadata={"run": self._run_name, "dataset": self._dataset_name},
        )
        trace.span(
            name="retrieval",
            output=sample.retrieved_contexts,
        )
        trace.generation(
            name="generate",
            output=sample.answer,
            usage={"input": sample.input_tokens, "output": sample.output_tokens},
        )
        for metric_name, value in scores.items():
            self._lf.score(
                trace_id=trace.id,
                name=metric_name,
                value=value,
            )

    def flush(self) -> None:
        if self._enabled and self._lf is not None:
            self._lf.flush()
