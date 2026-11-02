"""Process uploaded files and clean up unreferenced blobs, every 30 seconds."""

from prefect import flow

from explorai_pipelines.flows import runtime
from explorai_pipelines.ingest import DEFAULT_EXTRACTORS, collect_blobs, reextract


@flow(name="process-pending")
def process_pending_flow() -> dict[str, int]:
    """Extract stored artifacts that have no version yet, then collect orphan blobs.

    Only unversioned artifacts are read (``only_unversioned``), so a pass over a
    fully processed corpus reads no blob. Extractor version upgrades are not done
    here: the catch-up at the end of each nightly batch and the manual
    ``reextract`` command handle them.

    The extraction runs under an advisory lock, so overlapping runs (a slow
    extraction, or the nightly catch-up) never work on the same files; a run that
    does not get the lock reports ``skipped`` and still does the cheap cleanup.
    """
    rt = runtime.build_runtime()
    counters = reextract(
        repo=rt.repo,
        store=rt.store,
        settings=rt.settings,
        extractors=DEFAULT_EXTRACTORS,
        only_unversioned=True,
    )
    counters["blobs_deleted"] = collect_blobs(rt.repo, rt.store)
    return counters
