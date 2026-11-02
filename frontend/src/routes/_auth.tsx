import { createFileRoute, Outlet } from "@tanstack/react-router";

export const Route = createFileRoute("/_auth")({
  component: AuthLayout,
});

function AuthLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[hsl(var(--muted))]">
      <div className="w-full max-w-md px-4">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold text-[hsl(var(--foreground))]">
            Explorai
          </h1>
        </div>
        <Outlet />
      </div>
    </div>
  );
}
