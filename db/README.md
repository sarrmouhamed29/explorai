# explorai-db

The database schema of Explorai: SQLAlchemy 2 models, engine and session helpers, and the Alembic migrations. Several packages share it, so it has one owner and one migration history (see ADR 0008).

## Tables

| Table | Role |
|---|---|
| `users` | An account (`email`, unique on its lowercase form, `password_hash`, `created_at`, `is_active`) |
| `sources` | A declared source (`kind`, `name`, `config`); migration 0003 adds the `upload` source |
| `raw_artifacts` | Downloaded bytes, immutable (`sha256`, `storage_key`, `owner_id`) |
| `documents` | A logical document (`source_id`, `external_id`, `owner_id`), its `title` and the `source_metadata` the source gave (authors, publication date, abstract for arXiv) |
| `document_versions` | Text extracted from one artifact by one `extractor_version`; `status` is `ok`, `failed` or `unextractable`; `duplicate_of` points at a canonical version |
| `sections` | Cleaned text split by heading, in `ordinal` order |
| `ingestion_runs` | One row per execution, with counters and errors |

`owner_id` is a nullable foreign key to `users`: `NULL` is the shared public corpus. Unique constraints that involve it use `NULLS NOT DISTINCT`. Deleting a document cascades to its versions (and their sections); a version that pointed at a deleted canonical version gets `duplicate_of = NULL`.

## Row-Level Security

Migration 0003 enables RLS (without `FORCE`) on `documents`, `raw_artifacts`, `document_versions` and `sections`, with policies for the role `explorai_app` (`NOLOGIN`, no `BYPASSRLS`). The identity is the transaction-local setting `app.user_id`, read by the SQL function `app_user()`. As `explorai_app`, a user reads public rows and their own, inserts and deletes only their own; versions and sections follow their document. The role has no privileges on `ingestion_runs`.

The table owner bypasses the policies, so the pipeline keeps ingesting for everyone. Any code that serves a user must query through `explorai_db.rls.scoped_session(factory, user_id)`, or through `ScopedSessions(factory).scoped(user_id)`, the wrapper the backend hands its repositories so they never hold the factory: it opens one transaction, runs `SET LOCAL ROLE explorai_app` and sets the identity, so both end with the transaction and a pooled connection never keeps them. The downgrade keeps the role, which is cluster-wide.

Migration 0004 adds `documents.created_at` (the keyset pagination key), backfilled from the fetch time of the oldest matching artifact (or the migration time), with an index on `(created_at DESC, id DESC)`. `make_engine` hides SQL parameters from error messages, so a failing statement never puts a password hash in a log.

Two limits to remember (ADR [0010](../docs/adr/0010-row-level-security-defence-in-depth.md)): a `Session` used after its `scoped_session` block runs again as the owner (RLS bypassed), so use it only inside the block; and `SET LOCAL ROLE` does not stop injected SQL from running `RESET ROLE`. `explorai_app` can read `users.password_hash` because login needs the lookup.

## Blob store

`explorai_db.blobs` is the content-addressed store shared by the API (writes uploads) and the pipelines (reads them, collects garbage). The key is the SHA-256 of the bytes, so `put` is idempotent and also refreshes the modification time of an existing blob (a collector must not delete an old orphan that a new upload just referenced). `keys_older_than(seconds)` lists candidates and `delete_if_older_than(key, seconds)` renames the file aside atomically, re-checks its age, then deletes it or restores it. Only the pipelines delete blobs; the API cannot know, under RLS, whether another owner's artifact shares one.

## Run the migrations

`DATABASE_URL` is the only input (a `postgresql://` URL). From the repository root, with the stack's PostgreSQL running (see the root README):

```bash
set -a; . infra/.env; set +a
uv run python -m explorai_db.migrate           # upgrade to head
uv run alembic -c db/alembic.ini current        # which revision is applied
uv run alembic -c db/alembic.ini revision --autogenerate -m "describe the change"
```

After autogenerating, read the migration: autogenerate misses things such as check constraints. Migrations live in `src/explorai_db/migrations/versions`, excluded from ruff because they are generated.

## Test

```bash
uv run pytest db/tests -m "not integration" --no-cov    # no services needed
uv run pytest db/tests                                   # needs DATABASE_URL (a PostgreSQL superuser)
```

Integration tests create a throwaway migrated database with `explorai_db.testing.temporary_database` and drop it afterwards, so they never touch your development data. Lint and types:

```bash
uv run ruff check db && uv run ruff format --check db
uv run mypy --config-file db/pyproject.toml db/src db/tests
```
