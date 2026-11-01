"""Errors of the RAG layer. Messages never carry secrets or user text."""


class ProviderError(Exception):
    """A provider call failed in a way retrying will not fix."""


class ProviderUnavailable(ProviderError):
    """Transient failure: timeout, 5xx, connection error."""


class ProviderRateLimited(ProviderUnavailable):
    """The provider throttled us (HTTP 429); transient too."""


class RetrievalUnavailable(Exception):
    """The vector store cannot be reached or is not ready."""
