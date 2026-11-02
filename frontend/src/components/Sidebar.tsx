import { Link } from "@tanstack/react-router";
import { BarChart2, FileText, MessageSquare } from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_LINKS = [
  { to: "/dashboard", label: "Dashboard", icon: BarChart2 },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/chat", label: "Chat", icon: MessageSquare },
] as const;

export function Sidebar() {
  return (
    <nav className="flex h-full w-60 flex-col border-r border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3 py-4">
      <p className="mb-6 px-2 text-lg font-semibold text-[hsl(var(--foreground))]">
        Explorai
      </p>
      <ul className="space-y-1">
        {NAV_LINKS.map(({ to, label, icon: Icon }) => (
          <li key={to}>
            <Link
              to={to}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-2 text-sm font-medium text-[hsl(var(--muted-foreground))] transition-colors hover:bg-[hsl(var(--background))] hover:text-[hsl(var(--foreground))]",
              )}
              activeProps={{
                className:
                  "bg-[hsl(var(--background))] text-[hsl(var(--primary))]",
              }}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
