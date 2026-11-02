import pytest

from explorai_eval.config import EvalSettings
from explorai_eval.langfuse_client import LangfuseClient
from explorai_eval.runner import EvalSample


def _sample() -> EvalSample:
    return EvalSample(
        question="Q?",
        answer="A",
        status="answered",
        retrieved_contexts=["ctx"],
        expected_quote="quote",
        expected_answer="A",
        latency_s=0.1,
        input_tokens=10,
        output_tokens=5,
    )


def test_disabled_when_host_empty() -> None:
    settings = EvalSettings(provider="fake", langfuse_host="")
    client = LangfuseClient(settings, "eval", "run-001", enabled=True)
    assert not client.enabled


def test_disabled_when_flag_false() -> None:
    settings = EvalSettings(provider="fake", langfuse_host="http://127.0.0.1:3000")
    client = LangfuseClient(settings, "eval", "run-001", enabled=False)
    assert not client.enabled


def test_enabled_when_host_and_flag() -> None:
    settings = EvalSettings(provider="fake", langfuse_host="http://127.0.0.1:3000")
    client = LangfuseClient(settings, "eval", "run-001", enabled=True)
    assert client.enabled


def test_record_sample_noop_when_disabled() -> None:
    settings = EvalSettings(provider="fake", langfuse_host="")
    client = LangfuseClient(settings, "eval", "r", enabled=True)
    client.record_sample(_sample(), {"faithfulness": 0.9})  # must not raise


def test_flush_noop_when_disabled() -> None:
    settings = EvalSettings(provider="fake", langfuse_host="")
    client = LangfuseClient(settings, "eval", "r", enabled=True)
    client.flush()  # must not raise


def test_record_sample_and_flush_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeTrace:
        id = "trace-1"

        def span(self, **kw: object) -> None:
            calls.append(("span", kw))

        def generation(self, **kw: object) -> None:
            calls.append(("gen", kw))

    class FakeLangfuse:
        def __init__(self, **kw: object) -> None:
            pass

        def trace(self, **kw: object) -> FakeTrace:
            calls.append(("trace", kw))
            return FakeTrace()

        def score(self, **kw: object) -> None:
            calls.append(("score", kw))

        def flush(self) -> None:
            calls.append(("flush", {}))

    import langfuse as _lf

    monkeypatch.setattr(_lf, "Langfuse", FakeLangfuse)

    settings = EvalSettings(provider="fake", langfuse_host="http://127.0.0.1:3000")
    client = LangfuseClient(settings, "eval", "run-001", enabled=True)
    client.record_sample(_sample(), {"faithfulness": 0.9})
    client.flush()

    assert any(c[0] == "trace" for c in calls)
    assert any(c[0] == "flush" for c in calls)
