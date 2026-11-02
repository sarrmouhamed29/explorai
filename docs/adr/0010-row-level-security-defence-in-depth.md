# 0010. Row-Level Security as defence in depth

- Status: Accepted
- Date: 2026-11-01

## Context

The central guarantee of sub-project 2 is that a user can never read, list or delete another user's documents. The classic failure is the IDOR (insecure direct object reference): a route takes a document id and forgets to check that it belongs to the caller. One forgotten `WHERE owner_id = ...` in any future query is enough to leak data, and RAG (sub-project 3) will add many more queries.

## Decision

Isolation is enforced twice, by independent mechanisms.

1. **Application layer.** Every repository query filters on the owner explicitly, so the intent is readable and testable. Someone else's document is indistinguishable from a missing one (`404`, never `403`, so the API does not confirm that an id exists). A public document on a write is `403`.
2. **PostgreSQL Row-Level Security.** Migration 0003 enables RLS on `documents`, `raw_artifacts`, `document_versions` and `sections`, with policies for the role `explorai_app` (`NOLOGIN`, no `BYPASSRLS`). Reads see `owner_id IS NULL` (the public corpus) or the caller's rows; inserts and deletes only the caller's rows; there is no update privilege. Versions and sections follow their document through a subquery, which is itself filtered by the documents policy. The identity is the transaction-local setting `app.user_id`, read by the SQL function `app_user()`; without it only public rows are visible.

Design choices inside this decision:

- **`SET LOCAL ROLE explorai_app` per transaction**, inside `explorai_db.rls.scoped_session`, together with `set_config('app.user_id', ..., true)`. The first idea was `SET ROLE` once per connection. With a connection pool a connection outlives requests, so a role or identity that ends only when the connection closes can leak from one request to the next. `SET LOCAL` and `set_config(..., true)` both end with the transaction, so a pooled connection can never keep either. There is also no reset step that could be forgotten.
- **`ENABLE` without `FORCE`.** The table owner bypasses RLS unless `FORCE` is set. The API never runs as the owner (it always switches role inside `scoped_session`), while the pipeline connects as the owner and ingests for everyone (public and any owner), so it must keep bypassing the policies. `FORCE` would break the pipeline or require a second privileged role. The price is that the guarantee depends on the API always switching role, which an **architecture test** enforces: it parses every backend module with `ast` and fails on calls to `sessionmaker`, `Session`, `make_engine`, `create_engine`, `get_session_factory`, on `.begin`, `.begin_nested` and `.connect`, and on imports from `explorai_db.session`, anywhere except `deps.py` (and `db.py`, which opens a raw health-probe connection and never touches documents). It also fails on any call whose callee name contains `factory` (`self._factory()`, `factory()`), since calling a session factory directly opens an owner session. Beyond the test, the raw factory is unreachable by construction: `deps.get_scoped_sessions` gives repositories an `explorai_db.rls.ScopedSessions`, which keeps the factory in a name-mangled slot and exposes one method, `scoped(user_id)`. Request code can only open a session through `scoped_session`.
- **`users` is not under RLS.** Login must look up an email before the user is known. Only the authentication repository reads it, and password hashes are never returned by the API.
- **No grants on `ingestion_runs`.** The API has no business with pipeline runs.
- **Foreign keys.** `owner_id` becomes a foreign key to `users`. Deleting a document must not trip over versions of the same owner that marked it canonical, so `document_versions.document_id` gets `ON DELETE CASCADE` and `document_versions.duplicate_of` gets `ON DELETE SET NULL`.
- **Blobs are not deleted by the API.** Storage is content-addressed, so two owners uploading the same bytes share one blob, but under RLS the API cannot see another owner's artifact and so cannot know whether a blob is still referenced. Deleting would risk destroying another user's file. The pipeline (owner role, sees everything) collects blobs that no artifact references and that are older than one hour (the grace period protects an upload whose rows are not committed yet).
- **The isolation suite** (`backend/tests/integration/test_isolation.py`) is the success criterion. It reads the app's declared routes from the OpenAPI schema and sweeps all of them, so a new route that is not covered makes it fail. A second test runs a raw, unfiltered `SELECT` as `explorai_app` with user A's identity and asserts that A's and the public rows are visible and no row of B comes back. With no identity only public rows appear, which `db/tests/integration/test_rls.py` checks.

## Alternatives considered

- **Application filters only.** Simple, but one forgotten filter leaks silently, and nothing would notice.
- **RLS only.** A role misconfiguration (a connection that runs as the owner) disables everything without any error, and queries hide their intent. Using both means each catches the failure of the other, and the test with the filter removed proves the policies on their own.
- **A separate login role with its own password for the API.** The cleanest boundary (see limitations below), but it needs secret handling, a second connection string and bootstrap scripts. Deferred to sub-project 10.
- **One schema or one database per user.** Strong isolation but impractical migrations and a shared public corpus that every tenant would need to see.
- **`FORCE ROW LEVEL SECURITY` plus a `BYPASSRLS` pipeline role.** Equivalent in effect, but it adds a second privileged role to manage for no gain while the pipeline is the owner anyway.

## Consequences

- **`SET LOCAL ROLE` is not a hard boundary.** Code that can run arbitrary SQL in the transaction can run `RESET ROLE` or change `app.user_id` with `set_config`. RLS here guards against application bugs (a forgotten filter), not against SQL injection. The defence against injection is parameterised queries only; a distinct login role would make it a real boundary (sub-project 10).
- **A session used after its `scoped_session` block runs as the owner.** The block commits and ends the transaction; if the same `Session` object is reused, SQLAlchemy begins a new transaction on the connection's login role, which is the owner, and RLS is bypassed. Sessions must therefore be used only inside the `with` block. The architecture test and the fact that repositories create one scope per method keep this from happening, but it is a convention to remember.
- **`explorai_app` can read every `password_hash`** in `users`, because `users` is not under RLS and login needs the lookup. This is accepted: the role is only reachable through `SET ROLE` from the owner connection, and no route returns a hash. It is one more reason the role would deserve its own login in production.
- **Policies are SQL, outside autogenerate.** Migration 0003 is partly hand-written; the downgrade keeps the cluster-wide role. A model change that adds a table with an owner must add its policies by hand, and the isolation suite must be extended.
- **Cross-tenant timing oracle on uploads.** RLS hides other owners' rows, but not the shared, content-addressed blob store underneath: `LocalBlobStore.put()` of bytes that anyone already stored is a cheap mtime refresh instead of a full write. A 20 MB upload of a file someone else already stored therefore answers measurably faster, which lets a user test whether a specific file is stored (not who stored it, nor anything about its rows). Not fixed now; the options are to always write the bytes (cost: I/O on every upload), to pad the response time, or per-owner blob keys (cost: no deduplication across owners).
- **Cost.** Every request runs two extra statements (`SET LOCAL ROLE`, `set_config`), and policy subqueries add a lookup on `documents` for every version and section row read. Not measured yet; the plan is to check it with `EXPLAIN` when sub-project 3 adds heavier queries.
