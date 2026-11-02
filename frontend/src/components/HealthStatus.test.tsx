import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "../test/server";
import { HealthStatus } from "./HealthStatus";

describe("HealthStatus", () => {
  it('shows "Backend ready" and each dependency as ok when /health/ready returns 200', async () => {
    server.use(
      http.get("/health/ready", () =>
        HttpResponse.json({
          status: "ok",
          checks: { postgres: "ok", redis: "ok" },
        }),
      ),
    );
    render(<HealthStatus />);

    expect(await screen.findByText("Backend ready")).toBeInTheDocument();
    expect(screen.getByText("postgres: ok")).toBeInTheDocument();
    expect(screen.getByText("redis: ok")).toBeInTheDocument();
  });

  it('shows "Backend unavailable" and marks postgres down when it returns 503 with postgres down', async () => {
    server.use(
      http.get("/health/ready", () =>
        HttpResponse.json(
          { status: "unavailable", checks: { postgres: "down", redis: "ok" } },
          { status: 503 },
        ),
      ),
    );
    render(<HealthStatus />);

    expect(await screen.findByText("Backend unavailable")).toBeInTheDocument();
    expect(screen.getByText("postgres: down")).toBeInTheDocument();
    expect(screen.getByText("redis: ok")).toBeInTheDocument();
  });

  it('shows "Backend unavailable" when the request fails at network level', async () => {
    server.use(http.get("/health/ready", () => HttpResponse.error()));
    render(<HealthStatus />);

    expect(await screen.findByText("Backend unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/: (ok|down)$/)).not.toBeInTheDocument();
  });

  it("shows a loading state before the first response", async () => {
    server.use(
      http.get("/health/ready", async () => {
        await new Promise(() => {}); // never resolves
      }),
    );
    render(<HealthStatus />);

    expect(screen.getByText("Checking backend...")).toBeInTheDocument();
    expect(screen.queryByText("Backend ready")).not.toBeInTheDocument();
    expect(screen.queryByText("Backend unavailable")).not.toBeInTheDocument();
  });
});
