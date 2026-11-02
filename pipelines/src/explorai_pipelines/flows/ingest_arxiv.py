"""Ingest the latest arXiv papers matching a search query."""

import functools

from prefect import flow, task

from explorai_pipelines.flows import runtime
from explorai_pipelines.flows.common import (
    RETRIES,
    RETRY_DELAYS_SECONDS,
    is_transient,
)
from explorai_pipelines.ingest import RunReport, run_batch
from explorai_pipelines.netsafe import FetchResult
from explorai_pipelines.sources.arxiv import SOURCE_KIND
from explorai_pipelines.sources.base import SourceItem


@task(
    retries=RETRIES,
    retry_delay_seconds=RETRY_DELAYS_SECONDS,
    retry_condition_fn=is_transient,
)
def list_arxiv_items(query: str, max_results: int) -> list[SourceItem]:
    return runtime.build_runtime().arxiv.list_items(query, max_results)


@task(
    retries=RETRIES,
    retry_delay_seconds=RETRY_DELAYS_SECONDS,
    retry_condition_fn=is_transient,
)
def fetch_item(item: SourceItem) -> FetchResult:
    rt = runtime.build_runtime()
    return rt.arxiv.fetch(item, rt.settings.max_download_bytes)


@flow(name="ingest-arxiv")
def ingest_arxiv_flow(query: str, max_results: int = 20) -> RunReport:
    rt = runtime.build_runtime()
    source_id = rt.repo.get_or_create_source(SOURCE_KIND, "arxiv", {})
    items = list_arxiv_items(query, max_results)
    work = [(item, functools.partial(fetch_item, item)) for item in items]
    return run_batch(
        work,
        repo=rt.repo,
        store=rt.store,
        source_id=source_id,
        settings=rt.settings,
    )
