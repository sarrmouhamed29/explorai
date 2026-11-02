# Sub-project 6: Frontend, design spec

Part of the master plan (`plan.md`). Date: 2026-11-02. Status: draft.

## 1. Goal

Build the React + TypeScript single-page application that gives users a complete interface to the Explorai RAG backend: authentication, document management, a chat-like Q&A interface with citations, and a dashboard showing usage statistics.

The success criterion from `plan.md`: a user uploads a document and gets a sourced answer from the interface.

## 2. Scope

**In scope**
- Authentication pages: login and signup.
- Document management page: paginated list, PDF upload, document detail with sections, delete.
- Chat page: chat-like interface backed by a persisted conversation history; citations displayed inline.
- Dashboard page: stat cards and activity summary sourced from a new `/api/v1/stats` endpoint.
- Two new backend endpoints (`GET /api/v1/stats`, `GET /api/v1/conversations`) and one new table (`conversations`) added in this sub-project.
- `POST /api/v1/ask` extended to persist each exchange in `conversations`.
- Routing with TanStack Router, server state with TanStack Query, Tailwind CSS + shadcn/ui components.
- Vitest + msw tests for every page and every mutation.
- OpenAPI client regenerated and committed after each backend route change.

**Out of scope**
- Multi-turn conversation context sent to the LLM (stateless API, each ask is independent; deferred to SP8 Agents).
- Data visualisation charts (recharts, d3; deferred to SP7 Analytics).
- End-to-end tests (Playwright/Cypress; deferred to SP10 Production).
- Password reset or email verification flows.
- Public document sharing UI (the API supports `scope: "public"` but no management UI is added).
- Mobile-specific responsive design (responsive enough to not break, not optimised).
- Dark mode.

## 3. Decisions and rejected alternatives

| Topic | Decision | Alternatives not chosen |
|---|---|---|
| Routing | TanStack Router — fully type-safe params and search params, natural integration with TanStack Query loaders | React Router v6 (less type-safe, no built-in loader/query integration) |
| Server state | TanStack Query — cache, mutations, invalidation, same TanStack ecosystem as the router | SWR (smaller API, less integrated with TanStack Router); manual fetch hooks (more boilerplate, no cache) |
| UI components | Tailwind CSS + shadcn/ui — Radix UI primitives, accessible by default, zero runtime overhead, common in modern portfolios | Mantine (fully styled, batteries included, but opinionated theming); CSS Modules (no external dependency, but more manual work) |
| Global state | None — TanStack Query cache holds all server state; local React state holds UI-only state | Zustand (overkill: no cross-component client state that Query does not cover) |
| Conversation persistence | New `conversations` table + backend endpoints — persistent across sessions and devices | localStorage (lost on cache clear or device change, not suitable for a portfolio demonstrating full-stack thinking) |
| Charts on dashboard | None in SP6 — stat cards and a recent activity table only | recharts/d3 (deferred to SP7 Analytics; adding them in SP6 would pull in a dependency before there is real data to display) |
| Multi-turn context | Not sent to LLM — the API is single-turn | Multi-turn conversation (deferred to SP8 Agents, which adds LangGraph and tool use) |

## 4. Route tree

```
/                           → redirect to /dashboard (authenticated) or /login
/_auth                      → unauthenticated layout (centred card, no sidebar)
  /login
  /signup
/_app                       → authenticated layout (sidebar + header)
  /dashboard
  /documents
  /documents/$documentId
  /chat
```

`/_app` has a `beforeLoad` hook that calls `GET /api/v1/auth/me` and throws a redirect to `/login` on 401. This is the single auth guard; no `<PrivateRoute>` wrapper is needed.

`/_auth` layout: centred card, logo, cross-links between login and signup.

`/_app` layout: left sidebar (Dashboard, Documents, Chat links), top header (user email, Logout button). Logout calls `POST /api/v1/auth/logout`, clears the TanStack Query cache, then redirects to `/login`.

## 5. Backend extension

### Migration 0007: `conversations` table

```sql
CREATE TABLE conversations (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      UUID        NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    question      TEXT        NOT NULL,
    answer        TEXT,
    status        TEXT        NOT NULL CHECK (status IN ('answered', 'insufficient_evidence')),
    citations     JSONB       NOT NULL DEFAULT '[]',
    input_tokens  INT         NOT NULL DEFAULT 0,
    output_tokens INT         NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE POLICY conversations_owner ON conversations
    USING (owner_id = current_setting('app.current_user_id')::uuid);
```

### `POST /api/v1/ask` change

After the existing response is built, the ask handler saves a row to `conversations` (question, answer, status, citations serialised as JSONB, input/output tokens from `usage`). The response schema is unchanged; the save is a side-effect invisible to the caller.

### `GET /api/v1/conversations`

Returns the authenticated user's conversations, keyset-paginated (same `cursor` + `limit` pattern as `GET /api/v1/documents`). Default `limit`: 20. Ordered by `created_at DESC`.

Response schema:
```json
{
  "items": [
    {
      "id": "uuid",
      "question": "string",
      "answer": "string | null",
      "status": "answered | insufficient_evidence",
      "citations": [{ "document_id", "document_title", "chunk_id", "quote", "section" }],
      "created_at": "ISO 8601"
    }
  ],
  "next_cursor": "string | null"
}
```

### `GET /api/v1/stats`

Returns aggregated statistics for the authenticated user's own data.

Response schema:
```json
{
  "document_count": 5,
  "indexed_count": 3,
  "conversation_count": 42,
  "answered_count": 38,
  "refusal_count": 4,
  "total_input_tokens": 15000,
  "total_output_tokens": 8000
}
```

Computed with a single SQL query joining `documents` and `conversations` under RLS; no denormalised counters.

### OpenAPI contract

After each new route is implemented, `backend/scripts/export_openapi.py` is re-run and `frontend/src/api/schema.d.ts` is regenerated with `pnpm gen:api`. Both files are committed together.

## 6. TanStack Query keys and invalidations

| Query key | Endpoint | Loaded in |
|---|---|---|
| `['me']` | `GET /api/v1/auth/me` | `/_app` beforeLoad |
| `['documents']` | `GET /api/v1/documents` | `/documents` loader |
| `['document', id]` | `GET /api/v1/documents/:id` | `/documents/$documentId` loader |
| `['document', id, 'sections']` | `GET /api/v1/documents/:id/sections` | `/documents/$documentId` loader |
| `['stats']` | `GET /api/v1/stats` | `/dashboard` loader |
| `['conversations']` | `GET /api/v1/conversations` | `/chat` loader |

**Invalidations after mutations:**

| Mutation | Invalidates |
|---|---|
| Upload success | `['documents']`, `['stats']` |
| Delete success | `['documents']`, `['stats']` |
| Ask success | `['conversations']`, `['stats']` |
| Logout | entire Query cache |

## 7. Pages

### `/login` and `/signup`

Centred card with email and password fields. Client-side validation: required fields, basic email format. On submit, mutation calls the relevant auth endpoint; on success redirects to `/dashboard`. Error messages rendered inline below the form (not as toasts). Cross-link between the two pages.

### `/dashboard`

Four stat cards in a 2×2 grid (shadcn/ui `Card`):
- **Documents** — `document_count`
- **Indexed** — `indexed_count`
- **Questions** — `conversation_count`
- **Answer rate** — `answered_count / conversation_count` as a percentage

Two `Progress` bars below the cards:
- Token usage — input tokens vs output tokens as shares of total
- Outcome split — answered vs refusals

**Recent activity table** — last 5 conversations: question (truncated to 80 chars), status badge (green "Answered" / amber "Insufficient evidence"), date.

Data loaded by the `/dashboard` route loader via `['stats']` and a separate `['conversations']` fetch (reusing the existing key).

### `/documents`

Paginated table with columns: title (or filename if no title), status badge, upload date, actions (View → `/documents/$id`, Delete).

**Status badges:**
- `indexed: true` → green "Indexed"
- `indexed: false` → grey "Pending"
- `pii_blocked: true` → red "PII detected" (overrides others)

**Upload:** a "Upload PDF" button opens a shadcn/ui `<Dialog>`. The dialog contains a file input (`accept=".pdf"`) and a Submit button. On submit, the file is sent as `POST /api/v1/documents` with `Content-Type: application/pdf` (raw body, matching the existing API). On success, the dialog closes and `['documents']` + `['stats']` are invalidated.

**Delete:** per-row button opens an `<AlertDialog>` for confirmation. On confirm, `DELETE /api/v1/documents/:id` is called; on success `['documents']` + `['stats']` are invalidated.

**"Ask about this document" link** — visible in each row — navigates to `/chat?documentId=<id>` with the document pre-selected.

### `/documents/$documentId`

Two-column layout: left — document metadata (title, status badge, upload date, token counts if available); right — numbered sections list (title + first 200 chars of text).

Button "Ask about this document" navigates to `/chat?documentId=<id>`.

### `/chat`

Three-zone layout:

**Left column (1/3) — conversation history panel:**  
Loads `GET /api/v1/conversations` on mount. Each past exchange is a row (question truncated to 60 chars, status badge, date). Clicking a row populates the main zone with that exchange (read-only view of the past answer + citations).

**Main zone (2/3) — active exchange:**  
Shows the current question and its answer. Citations are displayed as numbered badges below the answer; clicking a badge expands a card showing the exact quote and the source document title. On `insufficient_evidence`, shows an amber callout "Insufficient evidence in your documents."

**Bottom bar:**  
- `<textarea>` for the question (max 2000 chars, matching the API limit)
- Scope selector: `all / mine / public` (shadcn/ui `<Select>`)
- Optional document selector (pre-populated from `?documentId` search param if present)
- Send button — disabled while a request is in flight; shows a spinner

During `POST /api/v1/ask`: textarea and Send are disabled. On 429: toast "Rate limit reached (30 questions/hour)." On success: new exchange appears in the main zone, `['conversations']` and `['stats']` are invalidated.

## 8. Error handling

| Situation | Behaviour |
|---|---|
| 401 from any authenticated endpoint | Global Query `onError` → clear cache, redirect to `/login`, toast "Session expired" |
| 422 validation error | Inline error message below the relevant field |
| 429 rate limit on `/ask` | Toast "Rate limit reached (30 questions/hour), try again later" |
| `pii_blocked: true` on a document | Red badge in the list; "Ask about this document" link hidden |
| Network error | Toast "Network error, please retry" |
| `insufficient_evidence` answer | Amber callout in the chat main zone (not an error, a normal response) |

## 9. Colour palette and theming

The five-colour "Productivité minimale" palette is applied via shadcn/ui's CSS variable system in `frontend/src/index.css`. shadcn/ui variables use HSL values without the `hsl()` wrapper.

| Token | Hex | HSL | Role |
|---|---|---|---|
| `--color-1` | `#FFFFFF` | `0 0% 100%` | Page background |
| `--color-2` | `#F4F5F7` | `220 14% 96%` | Sidebar, card backgrounds, muted surfaces |
| `--color-3` | `#DFE1E6` | `220 9% 88%` | Borders, dividers, input outlines |
| `--color-4` | `#0052CC` | `218 100% 40%` | Primary actions (buttons, links, active nav) |
| `--color-5` | `#172B4D` | `215 56% 19%` | Headings, body text, sidebar text |

Mapping to shadcn/ui semantic tokens:

```css
/* frontend/src/index.css — :root */
--background:       0 0% 100%;        /* color-1 */
--card:             220 14% 96%;      /* color-2 */
--muted:            220 14% 96%;      /* color-2 */
--border:           220 9% 88%;       /* color-3 */
--input:            220 9% 88%;       /* color-3 */
--primary:          218 100% 40%;     /* color-4 */
--primary-foreground: 0 0% 100%;      /* white on blue */
--foreground:       215 56% 19%;      /* color-5 */
--card-foreground:  215 56% 19%;      /* color-5 */
--muted-foreground: 215 30% 40%;      /* color-5 lightened */
--ring:             218 100% 40%;     /* color-4 */
```

No dark mode in SP6 (explicitly out of scope). The palette is set once in `index.css`; no Tailwind config override is needed beyond the default shadcn/ui setup.

## 10. Component inventory

All interactive primitives come from shadcn/ui (built on Radix UI). Custom components are wrappers or compositions.

| Component | shadcn/ui primitive(s) | Notes |
|---|---|---|
| `StatCard` | `Card` | Value + label + optional icon |
| `DocumentTable` | `Table` | Sortable by date |
| `StatusBadge` | `Badge` | Colour-coded by status |
| `UploadDialog` | `Dialog` | File input + submit |
| `DeleteDialog` | `AlertDialog` | Confirmation step |
| `ConversationHistory` | (list) | Left panel in /chat |
| `CitationBadge` | `Badge` + `Popover` | Numbered; hover/click shows quote |
| `ChatInputBar` | `Textarea`, `Select`, `Button` | Bottom bar in /chat |
| `InsufficientEvidenceCallout` | `Alert` | Amber, shown on refusal |

## 11. Testing strategy

Existing test infrastructure: Vitest + msw (`src/test/server.ts` + `src/test/setup.ts`). The msw server gains one handler per new endpoint.

**New msw handlers:**
- `GET /api/v1/auth/me` — returns a fixed user object or 401
- `POST /api/v1/auth/login` — returns 200 or 422
- `POST /api/v1/auth/signup` — returns 201 or 422
- `GET /api/v1/documents` — returns a fixed list
- `POST /api/v1/documents` — returns 201
- `DELETE /api/v1/documents/:id` — returns 204
- `GET /api/v1/documents/:id` — returns a fixed document
- `GET /api/v1/documents/:id/sections` — returns fixed sections
- `GET /api/v1/stats` — returns fixed stats
- `GET /api/v1/conversations` — returns a fixed list
- `POST /api/v1/ask` — returns a fixed `AskResponse`

**Test coverage per page:**
- Login/Signup: renders form, submits, redirects on success, shows error on 422.
- Dashboard: renders stat cards with mocked stats, renders recent activity table.
- Documents: renders list, opens upload dialog, submits file, opens delete dialog, confirms delete.
- Document detail: renders metadata and sections.
- Chat: renders history panel, sends question, displays answer with citations, shows refusal callout on `insufficient_evidence`, shows rate-limit toast on 429.

**Backend tests (new endpoints):**
- Unit tests for `ConversationRepository` (same pattern as `DocumentRepository`).
- Integration tests for `GET /api/v1/conversations` and `GET /api/v1/stats` (same pattern as document integration tests).
- RLS isolation test for conversations (`backend/tests/integration/test_isolation.py` extended).

## 12. Evaluation criteria

- All Vitest tests pass and coverage gate (80%) holds for the frontend.
- All backend unit and integration tests pass (conversations isolation included).
- `pnpm typecheck` passes with no errors.
- `pnpm lint` and `pnpm format:check` pass.
- The OpenAPI schema is regenerated and the generated TypeScript client matches the new routes.
- A user can manually: sign up, log in, upload a PDF, ask a question, see an answer with citations, see the dashboard update with the new stats.
- CI passes on `feature/frontend`.

## 13. Threats and guardrails

| Threat | Guardrail |
|---|---|
| Stale OpenAPI client after backend route changes | `gen:api` is run and both files are committed in the same task as the route; CI fails on drift (existing check) |
| RLS bypass on `conversations` | Isolation test added to `test_isolation.py`; same pattern as documents |
| `POST /ask` save fails silently and loses conversation | Save error is logged but does not propagate to the caller (the answer has already been given); a missing row is a minor data loss, not a user-visible error |
| PII in conversation content | Conversations store the answer after the output filter has run; the input filter has already rejected injections; stored content is as safe as what the API returned |
| Rate-limit confusion | 429 on `/ask` surfaces a clear toast with the 30/h limit; the frontend does not retry automatically |
| TypeScript ^5 constraint | `openapi-typescript` v7 is already in use and works with TypeScript ^5; TanStack Router and Query both support TypeScript ^5 |
| shadcn/ui component drift | shadcn/ui components are copied into `frontend/src/components/ui/` at install time; they are not a runtime dependency and do not auto-update |
