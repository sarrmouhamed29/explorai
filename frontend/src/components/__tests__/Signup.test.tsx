import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import SignupPage from "@/routes/_auth.signup";

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

test("renders email, password, and confirm-password fields", () => {
  renderWithQuery(<SignupPage />);
  expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/^password/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/confirm/i)).toBeInTheDocument();
});

test("shows error when passwords do not match", async () => {
  renderWithQuery(<SignupPage />);
  await userEvent.type(screen.getByLabelText(/email/i), "u@example.com");
  await userEvent.type(screen.getByLabelText(/^password/i), "abc");
  await userEvent.type(screen.getByLabelText(/confirm/i), "xyz");
  await userEvent.click(screen.getByRole("button", { name: /sign up/i }));
  await waitFor(() =>
    expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument(),
  );
});

test("redirects to /dashboard on successful signup", async () => {
  renderWithQuery(<SignupPage />);
  await userEvent.type(screen.getByLabelText(/email/i), "new@user.com");
  await userEvent.type(screen.getByLabelText(/^password/i), "pass1234");
  await userEvent.type(screen.getByLabelText(/confirm/i), "pass1234");
  await userEvent.click(screen.getByRole("button", { name: /sign up/i }));
  await waitFor(() =>
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/dashboard" }),
  );
});

test("shows error on 422 from server", async () => {
  server.use(
    http.post("/api/v1/auth/signup", () =>
      HttpResponse.json({}, { status: 422 }),
    ),
  );
  renderWithQuery(<SignupPage />);
  await userEvent.type(screen.getByLabelText(/email/i), "taken@user.com");
  await userEvent.type(screen.getByLabelText(/^password/i), "pass1234");
  await userEvent.type(screen.getByLabelText(/confirm/i), "pass1234");
  await userEvent.click(screen.getByRole("button", { name: /sign up/i }));
  await waitFor(() =>
    expect(screen.getByText(/could not create account/i)).toBeInTheDocument(),
  );
});
