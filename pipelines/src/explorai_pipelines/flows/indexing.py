"""Keep the vector index in step with the database: every 30 s, and hourly pruning."""

import logging

from prefect import flow

from explorai_pipelines.flows import runtime
from explorai_pipelines.indexing import index_pending, prune_index

logger = logging.getLogger(__name__)


@flow(name="index-pending")
def index_pending_flow() -> dict[str, int]:
    """Chunk new eligible versions, embed pending chunks, upsert, record.

    Runs under ``INDEX_LOCK``: an overlapping run reports ``skipped``. A provider
    outage does not fail the run: it is reported in ``embedding_failed`` and a
    warning, and the remaining chunks wait for the next run.
    """
    rt = runtime.build_runtime()
    rag = runtime.require_rag(rt)
    counters = index_pending(rt.repo, rag.embedder, rag.index, rag.settings)
    if counters["embedding_failed"]:
        logger.warning(
            "embedding provider unavailable: %d chunks embedded, rest pending",
            counters["embedded"],
        )
    return counters


@flow(name="prune-index")
def prune_index_flow() -> dict[str, int]:
    """Delete points of the live collection whose chunk is no longer eligible."""
    rt = runtime.build_runtime()
    rag = runtime.require_rag(rt)
    return prune_index(rt.repo, rag.index, rag.settings)
