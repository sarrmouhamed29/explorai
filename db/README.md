# explorai-db

The database schema of Explorai: SQLAlchemy 2 models, engine and session helpers, and the Alembic migrations. Several packages share it, so it has one owner and one migration history (see ADR 0008).

## Tables

| Table | Role |
|---|---|
| `sources` | A declared source (`kind`, `name`, `config`) |
| `raw_artifacts` | Downloaded bytes, immutable (`sha256`, `storage_key`, `owner_id`) |
| `documents` | A logical document (`source_id`, `external_id`, `owner_id`), its `title` and the `source_metadata` the source gave (authors, publication date, abstract for arXiv) |
| `document_versions` | Text extracted from one artifact by one `extractor_version`; `status` is `ok`, `failed` or `unextractable`; `duplicate_of` points at a canonical version |
| `sections` | Cleaned text split by heading, in `ordinal` order |
| `ingestion_runs` | One row per execution, with counters and errors |

`owner_id` is a nullable UUID with no foreign key until sub-project 2 adds users: `NULL` is the shared public corpus. Unique constraints that involve it use `NULLS NOT DISTINCT`.

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
