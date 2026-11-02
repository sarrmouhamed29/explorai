import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Outlet, useNavigate } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";
import { Sidebar } from "@/components/Sidebar";

interface AppShellProps {
  user: { id: string; email: string };
}

async function logoutRequest() {
  await fetch("/api/v1/auth/logout", { method: "POST" });
}

export default function AppShell({ user }: AppShellProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const logout = useMutation({
    mutationFn: logoutRequest,
    onSettled: () => {
      queryClient.clear();
      void navigate({ to: "/login" });
    },
  });

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-[hsl(var(--border))] bg-[hsl(var(--background))] px-6 py-3">
          <span className="text-sm text-[hsl(var(--muted-foreground))]">
            {user.email}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
          >
            Logout
          </Button>
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
