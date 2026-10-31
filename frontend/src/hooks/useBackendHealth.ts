import { useEffect, useState } from "react";
import { client } from "../api/client";

export type BackendHealth = {
  state: "loading" | "ready" | "unavailable";
  checks?: Record<string, "ok" | "down">;
};

export function useBackendHealth(): BackendHealth {
  const [health, setHealth] = useState<BackendHealth>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    client
      .GET("/health/ready")
      .then(({ data, error }) => {
        if (cancelled) return;
        // 200 arrives in `data`; the documented 503 body arrives in `error`.
        const body = data ?? error;
        setHealth({
          state: data ? "ready" : "unavailable",
          checks: body?.checks,
        });
      })
      .catch(() => {
        // Network-level failure: no response body, hence no checks.
        if (!cancelled) setHealth({ state: "unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return health;
}
