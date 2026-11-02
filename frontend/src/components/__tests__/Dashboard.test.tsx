import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import DashboardPage from "@/routes/_app.dashboard";

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => vi.fn() };
});

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

test("renders stat cards with data from /api/v1/stats", async () => {
  server.use(
    http.get("/api/v1/stats", () =>
      HttpResponse.json({
        document_count: 3,
        indexed_count: 2,
        conversation_count: 10,
        answered_count: 8,
        refusal_count: 2,
        total_input_tokens: 500,
        total_output_tokens: 200,
      }),
    ),
  );
  renderWithQuery(<DashboardPage />);
  await waitFor(() => expect(screen.getByText("3")).toBeInTheDocument());
  expect(screen.getByText("10")).toBeInTheDocument();
  expect(screen.getByText("80%")).toBeInTheDocument();
});

test("renders recent activity table with last 5 conversations", async () => {
  server.use(
    http.get("/api/v1/conversations", () =>
      HttpResponse.json({
        items: Array.from({ length: 6 }, (_, i) => ({
          id: `id-${i}`,
          question: `Question ${i}`,
          answer: "A",
          status: "answered",
          citations: [],
          created_at: new Date().toISOString(),
        })),
        next_cursor: null,
      }),
    ),
  );
  renderWithQuery(<DashboardPage />);
  await waitFor(() => expect(screen.getAllByRole("row").length).toBe(6));
});

test("shows n/a answer rate when no conversations", async () => {
  renderWithQuery(<DashboardPage />);
  await waitFor(() => expect(screen.getByText(/n\/a/i)).toBeInTheDocument());
});
