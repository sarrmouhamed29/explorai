"""Claude through the official SDK: grounded answers by forced tool use.

Forcing the `answer_question` tool makes the model return structured JSON that we
validate strictly, instead of parsing free text. The SDK's own retries are disabled
in the factory: `retry_transient` is the only retry layer.
"""

import json
from collections.abc import Sequence
from typing import Any

import anthropic

from explorai_ai.errors import ProviderError, ProviderRateLimited, ProviderUnavailable
from explorai_ai.models import GeneratedAnswer, PromptSource, RawCitation, Usage
from explorai_ai.prompt import (
    ANSWER_TOOL,
    REWRITE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_user_message,
    neutralise,
)
from explorai_ai.retry import retry_transient

# Room for a long answer plus its verbatim quotes: a cut-off answer (stop reason
# max_tokens) is unusable and would fail the same way on every retry.
ANSWER_MAX_TOKENS = 4096
REWRITE_MAX_TOKENS = 256


def _call(client: Any, **kwargs: Any) -> Any:
    """One messages.create call, vendor exceptions mapped to ours."""
    try:
        return client.messages.create(**kwargs)
    except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
        raise ProviderUnavailable(
            f"anthropic unreachable: {type(exc).__name__}"
        ) from None
    except anthropic.APIStatusError as exc:
        status = exc.status_code
        if status == 429:
            raise ProviderRateLimited("anthropic rate limit (HTTP 429)") from None
        if status >= 500:  # includes 529 (overloaded)
            raise ProviderUnavailable(
                f"anthropic server error (HTTP {status})"
            ) from None
        raise ProviderError(f"anthropic rejected the request (HTTP {status})") from None
    except anthropic.APIError:  # e.g. APIResponseValidationError: no HTTP status
        raise ProviderError("anthropic returned an unusable response") from None


_BAD_STOP_REASONS = {
    "max_tokens": "the answer was cut off (max_tokens)",
    "model_context_window_exceeded": "the prompt exceeded the context window",
}
# The model declined to answer (its safety stop): a decision about this question
# and these sources, not an outage, so an explicit refusal rather than a 503.
REFUSAL_STOP_REASON = "refusal"


def _check_stop_reason(message: Any) -> None:
    reason = getattr(message, "stop_reason", None)
    if reason in _BAD_STOP_REASONS:
        raise ProviderError(_BAD_STOP_REASONS[reason])


def _usage(message: Any) -> Usage:
    usage = getattr(message, "usage", None)
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def _parse_answer(tool_input: Any, usage: Usage) -> GeneratedAnswer:
    if not isinstance(tool_input, dict):
        raise ProviderError("the answer tool input is not an object")
    status = tool_input.get("status")
    answer = tool_input.get("answer")
    raw_citations = tool_input.get("citations")
    if status not in ("answered", "insufficient"):
        raise ProviderError("the answer has an invalid status")
    if not isinstance(answer, str):
        raise ProviderError("the answer text is not a string")
    if not isinstance(raw_citations, list):
        raise ProviderError("the answer citations are not a list")
    citations: list[RawCitation] = []
    for item in raw_citations:
        source = item.get("source") if isinstance(item, dict) else None
        quote = item.get("quote") if isinstance(item, dict) else None
        if (
            not isinstance(source, int)
            or isinstance(source, bool)
            or source < 1
            or not isinstance(quote, str)
            or not quote.strip()
        ):
            raise ProviderError("the answer has a malformed citation")
        citations.append(RawCitation(source=source, quote=quote))
    return GeneratedAnswer(
        status=status, answer=answer, citations=citations, usage=usage
    )


class AnthropicGenerator:
    def __init__(
        self, client: Any, *, model: str, rewrite_model: str, timeout: float
    ) -> None:
        self._client = client
        self._model = model
        self._rewrite_model = rewrite_model
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"AnthropicGenerator(model={self._model!r})"

    def answer(self, question: str, sources: Sequence[PromptSource]) -> GeneratedAnswer:
        message = retry_transient(
            lambda: _call(
                self._client,
                model=self._model,
                max_tokens=ANSWER_MAX_TOKENS,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": build_user_message(question, sources)}
                ],
                tools=[ANSWER_TOOL],
                tool_choice={"type": "tool", "name": ANSWER_TOOL["name"]},
                timeout=self._timeout,
            )
        )
        if getattr(message, "stop_reason", None) == REFUSAL_STOP_REASON:
            # Whatever partial content came with it is ignored: no citation of
            # a refused answer is ever shown.
            return GeneratedAnswer("insufficient", "", [], _usage(message))
        _check_stop_reason(message)
        for block in getattr(message, "content", None) or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == ANSWER_TOOL["name"]
            ):
                return _parse_answer(block.input, _usage(message))
        raise ProviderError("the model did not call the answer tool")

    def rewrite_queries(self, question: str, max_queries: int) -> list[str]:
        message = retry_transient(
            lambda: _call(
                self._client,
                model=self._rewrite_model,
                max_tokens=REWRITE_MAX_TOKENS,
                temperature=0,
                system=REWRITE_SYSTEM_PROMPT.format(max_queries=max_queries),
                messages=[
                    {
                        "role": "user",
                        "content": f"<question>\n{neutralise(question)}\n</question>",
                    }
                ],
                timeout=self._timeout,
            )
        )
        text = "".join(
            block.text
            for block in getattr(message, "content", None) or []
            if getattr(block, "type", None) == "text"
            and isinstance(getattr(block, "text", None), str)
        )
        return _parse_queries(text, max_queries)


def _parse_queries(text: str, max_queries: int) -> list[str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):  # tolerate a fenced code block
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return []
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) and item.strip() for item in parsed
    ):
        return []
    return [item.strip() for item in parsed[:max_queries]]
