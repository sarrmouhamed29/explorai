import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import AppShell from "@/components/AppShell";

const mockNavigate = vi.fn();
const mockClear = vi.fn();

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useRouter: () => ({ navigate: mockNavigate }),
    Link: ({
      children,
      ...props
    }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { to?: string }) => (
      <a href={props.to} {...props}>
        {children}
      </a>
    ),
    Outlet: () => <div data-testid="outlet" />,
  };
});

const FAKE_USER = { id: "u1", email: "alice@example.org" };

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.clear = mockClear;
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  mockNavigate.mockReset();
  mockClear.mockReset();
});

test("renders sidebar links when authenticated", () => {
  renderWithQuery(<AppShell user={FAKE_USER} />);
  expect(screen.getByRole("link", { name: /dashboard/i })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /documents/i })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /chat/i })).toBeInTheDocument();
});

test("shows user email in header", () => {
  renderWithQuery(<AppShell user={FAKE_USER} />);
  expect(screen.getByText(FAKE_USER.email)).toBeInTheDocument();
});

test("clears query cache and navigates to /login on logout", async () => {
  renderWithQuery(<AppShell user={FAKE_USER} />);
  await userEvent.click(screen.getByRole("button", { name: /logout/i }));
  await waitFor(() =>
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/login" }),
  );
  expect(mockClear).toHaveBeenCalled();
});
