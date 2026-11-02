"""Errors of the RAG layer. Messages never carry secrets or user text."""


class ProviderError(Exception):
    """A provider call failed in a way retrying will not fix."""


class ProviderUnavailable(ProviderError):
    """Transient failure: timeout, 5xx, connection error."""


class ProviderRateLimited(ProviderUnavailable):
    """The provider throttled us (HTTP 429); transient too."""


class GenerationFailed(ProviderUnavailable):
    """Generation failed for a reason that is not transient (bad request, unusable
    reply). Raised by the answer pipeline around any such ``ProviderError``, so every
    provider failure that escapes ``AnswerPipeline.ask`` is a ``ProviderUnavailable``:
    the service cannot answer right now, and it is not the user's fault (HTTP 503).
    """


class RetrievalUnavailable(Exception):
    """The vector store cannot be reached or is not ready."""
