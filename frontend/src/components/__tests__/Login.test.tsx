import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import LoginPage from "@/routes/_auth.login";

const mockNavigate = vi.fn();

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

beforeEach(() => {
  mockNavigate.mockReset();
});

test("renders email and password fields", () => {
  renderWithQuery(<LoginPage />);
  expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
});

test("redirects to /dashboard on successful login", async () => {
  renderWithQuery(<LoginPage />);
  await userEvent.type(screen.getByLabelText(/email/i), "a@b.com");
  await userEvent.type(screen.getByLabelText(/password/i), "secret");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  await waitFor(() =>
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/dashboard" }),
  );
});

test("shows error message on 422", async () => {
  server.use(
    http.post("/api/v1/auth/login", () =>
      HttpResponse.json({}, { status: 422 }),
    ),
  );
  renderWithQuery(<LoginPage />);
  await userEvent.type(screen.getByLabelText(/email/i), "a@b.com");
  await userEvent.type(screen.getByLabelText(/password/i), "bad");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  await waitFor(() =>
    expect(screen.getByText(/invalid credentials/i)).toBeInTheDocument(),
  );
});
