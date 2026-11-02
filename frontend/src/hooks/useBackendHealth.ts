import { useEffect, useState } from "react";
import { client } from "../api/client";

export const POLL_INTERVAL_MS = 5000;

export type BackendHealth = {
  state: "loading" | "ready" | "unavailable";
  checks?: Record<string, "ok" | "down">;
};

export function useBackendHealth(): BackendHealth {
  const [health, setHealth] = useState<BackendHealth>({ state: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    // The next poll is scheduled only after the previous one settles, so slow
    // responses never pile up. The last state stays on screen until a new one
    // arrives: later refreshes never flip back to "loading".
    const poll = async () => {
      try {
        const { data, error } = await client.GET("/health/ready", {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        // 200 arrives in `data`; the documented 503 body arrives in `error`.
        const body = data ?? error;
        setHealth({
          state: data ? "ready" : "unavailable",
          checks: body?.checks,
        });
      } catch {
        if (controller.signal.aborted) return;
        // Network-level failure: no response body, hence no checks.
        setHealth({ state: "unavailable" });
      }
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);

  return health;
}
