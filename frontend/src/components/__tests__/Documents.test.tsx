import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import DocumentsPage from "@/routes/_app.documents";

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    useNavigate: () => vi.fn(),
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

const DOC_BASE = {
  id: "d1",
  source_kind: "upload",
  owned: true,
  status: "ready" as const,
  created_at: new Date().toISOString(),
};

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

test("renders document list", async () => {
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        items: [
          { ...DOC_BASE, title: "Paper 1", indexed: true, pii_blocked: false },
        ],
        next_cursor: null,
      }),
    ),
  );
  renderWithQuery(<DocumentsPage />);
  await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
  expect(screen.getByText("Indexed")).toBeInTheDocument();
});

test("hides Ask link when pii_blocked", async () => {
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        items: [
          { ...DOC_BASE, title: "Doc", indexed: true, pii_blocked: true },
        ],
        next_cursor: null,
      }),
    ),
  );
  renderWithQuery(<DocumentsPage />);
  await waitFor(() =>
    expect(screen.getByText("PII detected")).toBeInTheDocument(),
  );
  expect(screen.queryByRole("link", { name: /ask/i })).not.toBeInTheDocument();
});

test("opens upload dialog when Upload button is clicked", async () => {
  renderWithQuery(<DocumentsPage />);
  await userEvent.click(screen.getByRole("button", { name: /upload/i }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});
