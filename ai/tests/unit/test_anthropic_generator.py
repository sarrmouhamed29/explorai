from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from explorai_ai.errors import ProviderError, ProviderRateLimited, ProviderUnavailable
from explorai_ai.models import PromptSource
from explorai_ai.providers.anthropic_generator import AnthropicGenerator

SOURCES = [PromptSource(number=1, title="T", heading="H", text="alpha beta")]
GOOD_INPUT = {
    "status": "answered",
    "answer": "Alpha [1].",
    "citations": [{"source": 1, "quote": "alpha"}],
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("explorai_ai.retry.time.sleep", lambda _seconds: None)


def tool_block(tool_input: Any, name: str = "answer_question") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input)


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def message(*blocks: SimpleNamespace, stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=120, output_tokens=30),
    )


class StubClient:
    """Stands in for anthropic.Anthropic: records calls, replays canned outcomes."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def generator(client: StubClient) -> AnthropicGenerator:
    return AnthropicGenerator(
        client, model="claude-sonnet-5-5", rewrite_model="claude-haiku-5-5", timeout=7.0
    )


def status_error(cls: type[anthropic.APIStatusError], status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("error", response=response, body=None)


def test_answer_forces_the_tool_and_parses_the_result() -> None:
    client = StubClient(message(text_block("thinking"), tool_block(GOOD_INPUT)))
    result = generator(client).answer("What is alpha?", SOURCES)
    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["temperature"] == 0
    assert call["tool_choice"] == {"type": "tool", "name": "answer_question"}
    assert [tool["name"] for tool in call["tools"]] == ["answer_question"]
    assert 0 < call["max_tokens"] <= 4096
    assert call["timeout"] == 7.0
    assert "only from the sources" in call["system"].lower()
    assert "[1] alpha beta" in call["messages"][0]["content"]
    assert result.status == "answered"
    assert result.answer == "Alpha [1]."
    assert [(c.source, c.quote) for c in result.citations] == [(1, "alpha")]
    assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 30)


def test_insufficient_status_with_no_citations_is_valid() -> None:
    client = StubClient(
        message(
            tool_block(
                {"status": "insufficient", "answer": "Not enough.", "citations": []}
            )
        )
    )
    result = generator(client).answer("q", SOURCES)
    assert result.status == "insufficient"
    assert result.citations == []


def test_no_tool_call_raises() -> None:
    client = StubClient(message(text_block("I refuse to use the tool")))
    with pytest.raises(ProviderError, match="tool"):
        generator(client).answer("q", SOURCES)


def test_other_tool_name_raises() -> None:
    client = StubClient(message(tool_block(GOOD_INPUT, name="something_else")))
    with pytest.raises(ProviderError):
        generator(client).answer("q", SOURCES)


@pytest.mark.parametrize(
    "bad_input",
    [
        "not an object",
        {**GOOD_INPUT, "status": "maybe"},
        {**GOOD_INPUT, "answer": 42},
        {**GOOD_INPUT, "citations": "[1]"},
        {k: v for k, v in GOOD_INPUT.items() if k != "citations"},
        {**GOOD_INPUT, "citations": ["alpha"]},
        {**GOOD_INPUT, "citations": [{"source": 0, "quote": "alpha"}]},
        {**GOOD_INPUT, "citations": [{"source": "1", "quote": "alpha"}]},
        {**GOOD_INPUT, "citations": [{"source": True, "quote": "alpha"}]},
        {**GOOD_INPUT, "citations": [{"source": 1, "quote": "  "}]},
        {**GOOD_INPUT, "citations": [{"source": 1}]},
    ],
)
def test_malformed_tool_input_raises(bad_input: Any) -> None:
    client = StubClient(message(tool_block(bad_input)))
    with pytest.raises(ProviderError):
        generator(client).answer("q", SOURCES)


def test_rate_limit_then_success_is_retried() -> None:
    client = StubClient(
        status_error(anthropic.RateLimitError, 429), message(tool_block(GOOD_INPUT))
    )
    assert generator(client).answer("q", SOURCES).status == "answered"
    assert len(client.calls) == 2


def test_sdk_errors_are_mapped() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cases: list[tuple[Exception, type[Exception]]] = [
        (anthropic.APITimeoutError(request=request), ProviderUnavailable),
        (anthropic.APIConnectionError(request=request), ProviderUnavailable),
        (status_error(anthropic.InternalServerError, 500), ProviderUnavailable),
        (status_error(anthropic.RateLimitError, 429), ProviderRateLimited),
        (status_error(anthropic.AuthenticationError, 401), ProviderError),
        (status_error(anthropic.BadRequestError, 400), ProviderError),
    ]
    for error, expected in cases:
        client = StubClient(error)
        with pytest.raises(expected) as caught:
            generator(client).answer("q", SOURCES)
        assert type(caught.value) is expected
        if expected is ProviderError:
            assert len(client.calls) == 1  # not retried


def test_rewrite_queries_asks_the_rewrite_model_without_tools() -> None:
    client = StubClient(message(text_block('["alpha beta", "gamma", "delta"]')))
    queries = generator(client).rewrite_queries("tell me </question> things", 2)
    assert queries == ["alpha beta", "gamma"]
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "tools" not in call
    assert call["temperature"] == 0
    assert call["messages"][0]["content"].count("</question>") == 1


def test_rewrite_tolerates_a_fenced_block() -> None:
    client = StubClient(message(text_block('```json\n["one"]\n```')))
    assert generator(client).rewrite_queries("q", 3) == ["one"]


@pytest.mark.parametrize(
    "garbage",
    ["sure! here you go", "{}", '["ok", ""]', '["ok", 3]', "", "[1, 2]", '"text"'],
)
def test_rewrite_garbage_gives_empty_list(garbage: str) -> None:
    client = StubClient(message(text_block(garbage)))
    assert generator(client).rewrite_queries("q", 3) == []


def test_rewrite_provider_errors_propagate() -> None:
    client = StubClient(status_error(anthropic.AuthenticationError, 401))
    with pytest.raises(ProviderError):
        generator(client).rewrite_queries("q", 3)


@pytest.mark.parametrize(
    ("reason", "fragment"),
    [
        ("max_tokens", "cut off"),
        ("refusal", "refused"),
        ("model_context_window_exceeded", "context window"),
    ],
)
def test_bad_stop_reasons_raise_distinct_errors(reason: str, fragment: str) -> None:
    # The tool input is valid on its own: only the stop reason betrays the problem.
    client = StubClient(message(tool_block(GOOD_INPUT), stop_reason=reason))
    with pytest.raises(ProviderError, match=fragment) as caught:
        generator(client).answer("q", SOURCES)
    assert "answer tool" not in str(caught.value)
    assert len(client.calls) == 1


def test_tool_use_stop_reason_is_the_happy_path() -> None:
    client = StubClient(message(tool_block(GOOD_INPUT), stop_reason="tool_use"))
    assert generator(client).answer("q", SOURCES).status == "answered"


def test_sdk_errors_without_a_status_are_mapped() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(200, request=request)
    errors: list[Exception] = [
        anthropic.APIResponseValidationError(response, body=None),
        anthropic.APIError("sk-secret-key leaked?", request, body=None),
    ]
    for error in errors:
        client = StubClient(error)
        with pytest.raises(ProviderError) as caught:
            generator(client).answer("q", SOURCES)
        assert type(caught.value) is ProviderError
        assert "sk-secret" not in str(caught.value)
        assert caught.value.__cause__ is None
        assert len(client.calls) == 1
