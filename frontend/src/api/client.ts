import createClient from "openapi-fetch";
import type { paths } from "./schema";

// An absolute origin keeps requests valid outside the browser (jsdom tests) too;
// in development the Vite proxy forwards /health to the backend.
// `fetch` is resolved per call, not captured at creation, so test-time
// interception (msw patches globalThis.fetch after import) still applies.
export const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});
