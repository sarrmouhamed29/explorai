import { QueryCache, QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (_error, query) => {
      if (
        query.state.status === "error" &&
        (query.state.error as { status?: number })?.status === 401
      ) {
        window.location.href = "/login";
      }
    },
  }),
  defaultOptions: {
    queries: {
      retry: false,
      staleTime: 30_000,
    },
  },
});
