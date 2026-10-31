"""Ingest an explicit list of web pages."""

import functools
from urllib.parse import urlsplit

from prefect import flow, task

from explorai_pipelines.flows import runtime
from explorai_pipelines.flows.common import (
    RETRIES,
    RETRY_DELAYS_SECONDS,
    is_transient,
)
from explorai_pipelines.ingest import RunReport, run_batch
from explorai_pipelines.netsafe import FetchResult, UrlPolicy, fetch_bytes
from explorai_pipelines.sources.base import SourceItem
from explorai_pipelines.sources.web import SOURCE_KIND, WebSource


@task(
    retries=RETRIES,
    retry_delay_seconds=RETRY_DELAYS_SECONDS,
    retry_condition_fn=is_transient,
)
def fetch_page(item: SourceItem, allowed_domains: list[str]) -> FetchResult:
    rt = runtime.build_runtime()
    return fetch_bytes(
        rt.client,
        item.url,
        UrlPolicy(frozenset(allowed_domains)),
        rt.settings.max_download_bytes,
        resolve=rt.resolve,
    )


@flow(name="ingest-web")
def ingest_web_flow(urls: list[str]) -> RunReport:
    rt = runtime.build_runtime()
    source_id = rt.repo.get_or_create_source(SOURCE_KIND, "web", {})
    # The operator names the pages, so their hosts (and only those) are allowed.
    domains = sorted({(urlsplit(u).hostname or "").lower() for u in urls} - {""})
    work = [
        (item, functools.partial(fetch_page, item, domains))
        for item in WebSource(urls).list_items()
    ]
    return run_batch(
        work,
        repo=rt.repo,
        store=rt.store,
        source_id=source_id,
        settings=rt.settings,
    )
