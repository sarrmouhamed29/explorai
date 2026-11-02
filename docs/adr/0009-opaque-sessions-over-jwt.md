# 0009. Opaque server-side sessions instead of JWT

- Status: Accepted
- Date: 2026-11-01

## Context

Sub-project 2 adds accounts. After a login, the browser must prove who it is on every request, and the server must be able to end that proof (logout, expiry). The first consumer is a single-page app served from the same site as the API, so cookies are available. Passwords must be stored so that a database leak does not reveal them, and login must resist guessing.

## Decision

- **Passwords** are hashed with **Argon2id** (`argon2-cffi` defaults, RFC 9106 low-memory profile: 64 MiB per hash). Each hash carries its own random salt and its parameters inside the encoded string. Passwords are 12 to 128 characters with no composition rules (NIST SP 800-63B: length matters, and the upper bound stops huge inputs from loading the deliberately slow hash).
- **Sessions are opaque.** At login the server creates `secrets.token_urlsafe(32)` (256 bits of randomness), stores `session:<sha256 hex of the token>` in Redis with the user id and the creation time, and sends the raw token in a cookie. Only the hash is stored, so a Redis dump yields no usable token. A new token is created at every login, and the session the client came with is revoked (no session fixation).
- **Two expiries.** The idle expiry (24 h, `SESSION_IDLE_SECONDS`) is the Redis TTL, renewed on use. The absolute expiry (7 days, `SESSION_MAX_SECONDS`) is checked against the stored creation time, and the renewed TTL never reaches past it. Logout deletes the key, so it takes effect immediately.
- **Cookie** `explorai_session`: `HttpOnly` (scripts cannot read it, which limits what an XSS can steal), `Secure` (HTTPS only; `SESSION_COOKIE_SECURE=false` only for the plain-HTTP dev stack), `SameSite=Lax`, `Path=/`.
- **CSRF** is handled by two layers: `SameSite=Lax`, and a middleware that rejects `POST`, `PUT`, `PATCH` and `DELETE` whose `Origin` header is not in `ALLOWED_ORIGINS`. A request without `Origin` (curl, tests) is allowed: it is not a browser, so it is not a CSRF vector.
- **Login hardening.** One answer (`401 invalid credentials`) for every failure; a dummy hash is verified when the email is unknown so timing does not reveal which emails exist; a fixed-window limiter in Redis refuses the sixth failure for one email or the twenty-first for one IP within 15 minutes with `429`. The attempt is reserved with an atomic `INCR` before any hashing, so parallel requests cannot all slip under the limit.
- **Fail closed.** If Redis is unreachable the answer is `503`, never "assume logged in".

## Alternatives considered

- **Short-lived JWT plus refresh token.** Stateless verification is attractive for many services, but this system has one API and already runs Redis. A JWT cannot be revoked before its expiry without keeping a deny list (which is state again), the refresh-token rotation logic is a second mechanism to get right, and the browser needs somewhere to keep the tokens: `localStorage` is readable by any script (XSS), a cookie brings back CSRF anyway. An opaque session gives instant revocation with less to get wrong.
- **An external identity provider (Keycloak, Auth0, OIDC).** The mechanisms would be delegated rather than learned, and Keycloak is one more heavy service. It becomes reasonable when sign-in with Google or SSO is required; the `current_user` dependency is the single place that would change.
- **A framework such as FastAPI-Users.** Fast to start, but its schema and strategies dictate the design, fit row-level security poorly (ADR 0010), and hide the mechanisms this project exists to learn.
- **bcrypt or scrypt instead of Argon2id.** Both are acceptable. bcrypt is not memory-hard and truncates input at 72 bytes; scrypt is memory-hard but harder to tune. Argon2id is the current OWASP first choice.
- **Salted or slow hash for the session token.** Unnecessary: the token has 256 bits of entropy, so a plain SHA-256 cannot be reversed by guessing, unlike a human password.

## Consequences

- Every authenticated request costs one Redis round trip, and Redis is now on the critical path (hence the fail-closed `503`). Sessions are lost if Redis loses its data; users simply log in again.
- Known limitations, accepted and documented:
  - **Lockout by design.** The per-email limit lets anyone lock a given user out for 15 minutes by sending bad passwords for that email. The alternative (no per-email limit) lets an attacker guess against one account from many addresses.
  - **Account enumeration at sign-up.** `409 email already registered` reveals that an account exists. Closing it needs email confirmation, which is out of scope.
  - **The IP limit** uses the address the server sees; behind a reverse proxy it must be configured to see the client's, which is production work (sub-project 10). A success does not count against the IP, since several users may share one address.
  - **The fixed window** allows up to twice the limit across a window boundary; irrelevant at this scale for brute force, and O(1) in state.
  - No password reset, no email confirmation, no "log out everywhere" (a user-to-sessions index would be needed).
- The upload limiter reuses the same limiter class (20 uploads per hour per user), which gives a soft quota: a burst can overshoot the 200-document cap by up to about 19 documents, and the bandwidth of rejected uploads is not limited by the API (a reverse proxy should do it).
