import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import DocumentDetailPage from "@/routes/_app.documents.$documentId";

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ documentId: "d1" }),
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
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

test("renders document title and sections", async () => {
  server.use(
    http.get("/api/v1/documents/d1", () =>
      HttpResponse.json({
        id: "d1",
        title: "My Paper",
        indexed: true,
        pii_blocked: false,
        source_kind: "upload",
        owned: true,
        status: "ready",
        created_at: new Date().toISOString(),
      }),
    ),
    http.get("/api/v1/documents/d1/sections", () =>
      HttpResponse.json({
        items: [
          {
            ordinal: 1,
            heading: "Introduction",
            text: "Lorem ipsum dolor sit amet.",
          },
        ],
      }),
    ),
  );
  renderWithQuery(<DocumentDetailPage />);
  await waitFor(() => expect(screen.getByText("My Paper")).toBeInTheDocument());
  expect(screen.getByText(/introduction/i)).toBeInTheDocument();
});
