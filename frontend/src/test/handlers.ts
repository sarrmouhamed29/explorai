import { http, HttpResponse } from "msw";

const FAKE_USER = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "test@example.org",
};
const FAKE_DOC = {
  id: "00000000-0000-0000-0000-000000000002",
  owner_id: FAKE_USER.id,
  title: "Test Doc",
  status: "ok",
  indexed: false,
  created_at: "2026-01-01T00:00:00Z",
};

export const handlers = [
  http.post("/api/v1/auth/login", () =>
    HttpResponse.json(FAKE_USER, { status: 200 }),
  ),
  http.post("/api/v1/auth/signup", () =>
    HttpResponse.json(FAKE_USER, { status: 201 }),
  ),
  http.post("/api/v1/auth/logout", () =>
    HttpResponse.json({}, { status: 200 }),
  ),
  http.get("/api/v1/auth/me", () =>
    HttpResponse.json(FAKE_USER, { status: 200 }),
  ),
  http.get("/api/v1/documents", () =>
    HttpResponse.json({ items: [], next_cursor: null }, { status: 200 }),
  ),
  http.post("/api/v1/documents", () =>
    HttpResponse.json(FAKE_DOC, { status: 201 }),
  ),
  http.delete(
    "/api/v1/documents/:id",
    () => new HttpResponse(null, { status: 204 }),
  ),
  http.get("/api/v1/documents/:id", () =>
    HttpResponse.json(FAKE_DOC, { status: 200 }),
  ),
  http.get("/api/v1/documents/:id/sections", () =>
    HttpResponse.json({ items: [] }, { status: 200 }),
  ),
  http.get("/api/v1/stats", () =>
    HttpResponse.json(
      {
        document_count: 0,
        indexed_count: 0,
        conversation_count: 0,
        answered_count: 0,
        refusal_count: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
      },
      { status: 200 },
    ),
  ),
  http.get("/api/v1/conversations", () =>
    HttpResponse.json({ items: [], next_cursor: null }, { status: 200 }),
  ),
  http.post("/api/v1/ask", () =>
    HttpResponse.json(
      {
        status: "answered",
        answer: "Test answer.",
        citations: [],
        usage: { input_tokens: 10, output_tokens: 5 },
      },
      { status: 200 },
    ),
  ),
];
