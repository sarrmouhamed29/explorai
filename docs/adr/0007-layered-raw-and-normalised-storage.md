# 0007. Layered storage: immutable raw artifacts, then versioned text

- Status: Accepted
- Date: 2026-10-31

## Context

Ingestion turns PDFs and web pages into text. Extraction quality will change over the life of the project (better column handling, layout models, OCR), and each change must be measurable against the previous one. Sources such as arXiv rate-limit downloads, so re-downloading to re-extract is slow and impolite. A result must also be reproducible: the same input must give the same stored output.

## Decision

Store in two layers.

1. **Raw artifact.** The downloaded bytes are kept unchanged on a content-addressed blob store (`BlobStore`; the key is the SHA-256 of the bytes; `LocalBlobStore` writes to a volume). A `raw_artifacts` row records the source, external id, URL, hash and owner.
2. **Versioned text.** Extraction produces a `document_versions` row (one per artifact and `extractor_version`) with its `sections`. A new extractor version creates new versions from the stored bytes, with no network access; old versions stay.

Idempotence follows from the identities: an artifact is `(source, external_id, owner, sha256)`, a version is `(artifact, extractor_version)`. Replaying a batch finds both and changes nothing. Duplicates are marked (`duplicate_of`, always pointing at a canonical version), never deleted.

## Alternatives considered

- **Single pass, storing only the text.** Simpler and smaller. But improving the extractor means downloading everything again, results of different extractor generations cannot be compared, and a run cannot be reproduced.
- **Queue-driven workers (Redis).** Scales better, but adds moving parts with no current need; scalability is sub-project 11.
- **Raw bytes in PostgreSQL (`bytea`).** One system fewer, but large objects bloat the database and its backups. The blob interface makes S3 or MinIO a later swap.

## Consequences

- More storage (raw plus text) and two levels of code. In exchange, extraction can be replayed and compared, which sub-project 11 needs.
- Uniqueness involving the nullable `owner_id` uses PostgreSQL `NULLS NOT DISTINCT`; without it, `NULL` owners (the shared public corpus) would escape every unique constraint.
- Local volumes do not survive a move to several machines; the `BlobStore` interface is the seam for S3 in sub-project 10.
