"""Re-run extraction on stored artifacts after an extractor version bump."""

from prefect import flow

from explorai_pipelines.flows import runtime
from explorai_pipelines.ingest import DEFAULT_EXTRACTORS
from explorai_pipelines.ingest import reextract as reextract_artifacts


@flow(name="reextract")
def reextract_flow(extractor_version: str) -> dict[str, int]:
    selected = {
        kind: extractor
        for kind, extractor in DEFAULT_EXTRACTORS.items()
        if extractor.version == extractor_version
    }
    if not selected:
        known = sorted(e.version for e in DEFAULT_EXTRACTORS.values())
        raise ValueError(
            f"unknown extractor version: {extractor_version!r} (known: {known})"
        )
    rt = runtime.build_runtime()
    return reextract_artifacts(
        repo=rt.repo, store=rt.store, settings=rt.settings, extractors=selected
    )
