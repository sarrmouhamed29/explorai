"""Asking a question: the hourly budget, the visibility check, the answer pipeline.

The pipeline calls paid providers, so every question first reserves one unit of
the user's hourly budget (an atomic ``INCR``, as for uploads). Only a question
that was actually served keeps its reservation, an answer or an explicit refusal
alike; every other outcome (over the limit, document not visible, provider or
database outage, a bug) gives it back, so the user does not pay for our failures.

Nothing here logs the question or the answer: error types only.
"""

import logging
import uuid
from typing import Protocol

import redis

from explorai_ai.errors import ProviderUnavailable, RetrievalUnavailable
from explorai_ai.models import AnswerResult
from explorai_ai.scope import Scope
from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.repositories.documents import DocumentRow
from explorai_backend.services.documents import DocumentNotFound

logger = logging.getLogger(__name__)


class TooManyQuestions(Exception):
    """The user's hourly budget of questions is spent."""


class RateLimiterUnavailable(Exception):
    """Redis cannot count the question: fail closed, nothing is asked."""


class AnswerUnavailable(Exception):
    """The vector store or a model provider cannot serve the question now."""


class Answers(Protocol):
    """What the service needs from ``AnswerPipeline`` (a fake stands in for tests)."""

    def ask(
        self,
        user_id: uuid.UUID,
        question: str,
        scope: Scope = "all",
        document_id: uuid.UUID | None = None,
    ) -> AnswerResult: ...


class VisibleDocuments(Protocol):
    """What the service needs from ``DocumentsRepository``."""

    def get(self, user_id: uuid.UUID, document_id: uuid.UUID) -> DocumentRow | None: ...


class AskService:
    def __init__(
        self,
        pipeline: Answers,
        documents: VisibleDocuments,
        limiter: FixedWindowLimiter,
    ) -> None:
        self._pipeline = pipeline
        self._documents = documents
        self._limiter = limiter

    def ask(
        self,
        user_id: uuid.UUID,
        question: str,
        scope: Scope = "all",
        document_id: uuid.UUID | None = None,
    ) -> AnswerResult:
        """Answer, or raise one of this module's errors or ``DocumentNotFound``.

        Database errors (``OperationalError``, pool timeout) propagate after the
        reservation is given back; the app's handlers answer them with 503.
        """
        key = str(user_id)
        try:
            reserved = self._limiter.hit(key)
        except redis.RedisError:
            logger.warning("rate limiter unavailable")
            raise RateLimiterUnavailable from None
        served = False
        try:
            if reserved > self._limiter.limit:
                raise TooManyQuestions
            # Same check as GET /documents/{id}: someone else's private document
            # and an unknown id are the same 404, so no existence oracle.
            if (
                document_id is not None
                and self._documents.get(user_id, document_id) is None
            ):
                raise DocumentNotFound
            try:
                result = self._pipeline.ask(user_id, question, scope, document_id)
            except (ProviderUnavailable, RetrievalUnavailable) as exc:
                # GenerationFailed is a ProviderUnavailable. The type only, no text.
                logger.warning("ask unavailable: %s", type(exc).__name__)
                raise AnswerUnavailable from None
            served = True
            return result
        finally:
            if not served:
                self._release(key)

    def _release(self, key: str) -> None:
        try:
            self._limiter.release(key)
        except redis.RedisError:
            # Errs on the strict side: one extra count until the window ends.
            logger.warning("could not release an ask reservation")
