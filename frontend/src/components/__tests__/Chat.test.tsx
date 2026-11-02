import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Toaster } from "sonner";

import { server } from "@/test/server";
import ChatPage from "@/routes/_app.chat";

let mockSearch: Record<string, string | undefined> = {};

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useSearch: () => mockSearch,
    Link: ({
      children,
      ...props
    }: React.AnchorHTMLAttributes<HTMLAnchorElement> & {
      to?: string;
      search?: unknown;
    }) => (
      <a href={props.to} {...props}>
        {children}
      </a>
    ),
  };
});

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Toaster />
      {ui}
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockSearch = {};
});

test("renders empty conversation history with zero-state message", async () => {
  renderWithQuery(<ChatPage />);
  await waitFor(() =>
    expect(screen.getByText(/no conversations yet/i)).toBeInTheDocument(),
  );
});

test("sends question and displays answer with citations", async () => {
  server.use(
    http.post("/api/v1/ask", () =>
      HttpResponse.json({
        status: "answered",
        answer: "The answer is 42.",
        citations: [
          {
            source: 1,
            quote: "answer is 42",
            chunk_id: "c1",
            document_id: "d1",
            document_title: "Guide",
            section: null,
          },
        ],
        usage: { input_tokens: 10, output_tokens: 5 },
      }),
    ),
  );
  renderWithQuery(<ChatPage />);
  await userEvent.type(screen.getByRole("textbox"), "What is the answer?");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() =>
    expect(screen.getByText("The answer is 42.")).toBeInTheDocument(),
  );
  expect(screen.getByText("1")).toBeInTheDocument(); // citation badge
});

test("shows insufficient evidence callout on refusal", async () => {
  server.use(
    http.post("/api/v1/ask", () =>
      HttpResponse.json({
        status: "insufficient_evidence",
        answer: null,
        citations: [],
        usage: { input_tokens: 5, output_tokens: 0 },
      }),
    ),
  );
  renderWithQuery(<ChatPage />);
  await userEvent.type(screen.getByRole("textbox"), "Unanswerable?");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() =>
    expect(screen.getByText(/insufficient evidence/i)).toBeInTheDocument(),
  );
});

test("shows rate-limit error on 429", async () => {
  server.use(
    http.post("/api/v1/ask", () => HttpResponse.json({}, { status: 429 })),
  );
  renderWithQuery(<ChatPage />);
  await userEvent.type(screen.getByRole("textbox"), "Q");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() =>
    expect(screen.getByText(/rate limit/i)).toBeInTheDocument(),
  );
});

test("pre-populates document selector from ?documentId search param", async () => {
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        items: [
          {
            id: "d1",
            title: "My Doc",
            indexed: true,
            pii_blocked: false,
            source_kind: "upload",
            owned: true,
            status: "ready",
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
        next_cursor: null,
      }),
    ),
  );
  renderWithQuery(<ChatPage searchParams={{ documentId: "d1" }} />);
  await waitFor(() => {
    const selector = screen.getByRole("combobox", { name: /document/i });
    expect(selector).toHaveValue("d1");
  });
});

test("send button re-enables after insufficient_evidence", async () => {
  server.use(
    http.post("/api/v1/ask", () =>
      HttpResponse.json({
        status: "insufficient_evidence",
        answer: null,
        citations: [],
        usage: { input_tokens: 5, output_tokens: 0 },
      }),
    ),
  );
  renderWithQuery(<ChatPage />);
  await userEvent.type(screen.getByRole("textbox"), "Q");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /send/i })).not.toBeDisabled(),
  );
});
