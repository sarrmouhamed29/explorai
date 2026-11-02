import { createFileRoute, redirect } from "@tanstack/react-router";

import AppShell from "@/components/AppShell";

interface AuthMe {
  id: string;
  email: string;
}

export const Route = createFileRoute("/_app")({
  beforeLoad: async () => {
    const res = await fetch("/api/v1/auth/me");
    if (!res.ok) {
      throw redirect({ to: "/login" });
    }
    const user = (await res.json()) as AuthMe;
    return { user };
  },
  component: AppShellRoute,
});

function AppShellRoute() {
  const { user } = Route.useRouteContext();
  return <AppShell user={user} />;
}
