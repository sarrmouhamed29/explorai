import { act, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HealthStatus } from "../components/HealthStatus";
import { server } from "../test/server";
import { POLL_INTERVAL_MS } from "./useBackendHealth";

const ready = { status: "ok", checks: { postgres: "ok", redis: "ok" } };
const postgresDown = {
  status: "unavailable",
  checks: { postgres: "down", redis: "ok" },
};

// Testing Library's findBy*/waitFor wait on a real setTimeout(0) that fake timers
// would freeze, so these tests drive time by hand with tick() and assert synchronously.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => {
  vi.useRealTimers();
});

// Lets the pending fetch + React update settle, without moving the poll clock.
async function settle() {
  await act(async () => {
    for (let i = 0; i < 20; i++) await vi.advanceTimersByTimeAsync(0);
  });
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("useBackendHealth polling", () => {
  it("fetches immediately, then refreshes: a 200 turns into a 503 with postgres down", async () => {
    let calls = 0;
    server.use(
      http.get("/health/ready", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json(ready)
          : HttpResponse.json(postgresDown, { status: 503 });
      }),
    );
    render(<HealthStatus />);

    await settle();
    expect(screen.getByText("Backend ready")).toBeInTheDocument();
    expect(calls).toBe(1);

    await tick(POLL_INTERVAL_MS);
    await settle();
    expect(screen.getByText("Backend unavailable")).toBeInTheDocument();
    expect(screen.getByText("postgres: down")).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it("recovers: a 503 turns back into ready on a later poll", async () => {
    let calls = 0;
    server.use(
      http.get("/health/ready", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json(postgresDown, { status: 503 })
          : HttpResponse.json(ready);
      }),
    );
    render(<HealthStatus />);

    await settle();
    expect(screen.getByText("Backend unavailable")).toBeInTheDocument();

    await tick(POLL_INTERVAL_MS);
    await settle();
    expect(screen.getByText("Backend ready")).toBeInTheDocument();
    expect(screen.getByText("postgres: ok")).toBeInTheDocument();
  });

  it("keeps showing the last state, not loading, while a refresh is in flight", async () => {
    let calls = 0;
    server.use(
      http.get("/health/ready", async () => {
        calls += 1;
        if (calls === 1) return HttpResponse.json(ready);
        await new Promise(() => {}); // the refresh never answers
      }),
    );
    render(<HealthStatus />);
    await settle();
    expect(screen.getByText("Backend ready")).toBeInTheDocument();

    await tick(POLL_INTERVAL_MS);

    expect(calls).toBe(2);
    expect(screen.getByText("Backend ready")).toBeInTheDocument();
    expect(screen.queryByText("Checking backend...")).not.toBeInTheDocument();
  });

  it("stops polling after unmount", async () => {
    let calls = 0;
    server.use(
      http.get("/health/ready", () => {
        calls += 1;
        return HttpResponse.json(ready);
      }),
    );
    const { unmount } = render(<HealthStatus />);
    await settle();
    expect(screen.getByText("Backend ready")).toBeInTheDocument();
    expect(calls).toBe(1);

    unmount();
    await tick(POLL_INTERVAL_MS * 4);

    expect(calls).toBe(1);
  });
});
