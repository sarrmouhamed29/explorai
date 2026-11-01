"""The single place where a Qdrant filter is built.

Every search goes through build_scope_filter, so the owner condition cannot be
forgotten by a caller: one function to review for tenant isolation, and an AST test
forbids building filters anywhere else.
"""

import uuid
from typing import Literal

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

PUBLIC_OWNER = "public"
Scope = Literal["all", "mine", "public"]


def build_scope_filter(
    user_id: uuid.UUID,
    scope: Scope = "all",
    document_id: uuid.UUID | None = None,
) -> Filter:
    owner: MatchAny | MatchValue
    if scope == "all":
        owner = MatchAny(any=[str(user_id), PUBLIC_OWNER])
    elif scope == "mine":
        owner = MatchValue(value=str(user_id))
    else:
        owner = MatchValue(value=PUBLIC_OWNER)
    must: list[FieldCondition] = [FieldCondition(key="owner_id", match=owner)]
    if document_id is not None:
        must.append(
            FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))
        )
    return Filter(must=list(must))
