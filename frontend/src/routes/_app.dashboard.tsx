import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { StatCard } from "@/components/StatCard";

export const Route = createFileRoute("/_app/dashboard")({
  component: DashboardPage,
});

interface StatsOut {
  document_count: number;
  indexed_count: number;
  conversation_count: number;
  answered_count: number;
  refusal_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
}

interface ConversationItem {
  id: string;
  question: string;
  answer: string | null;
  status: string;
  created_at: string;
}

async function fetchStats(): Promise<StatsOut> {
  const res = await fetch("/api/v1/stats");
  if (!res.ok)
    throw Object.assign(new Error("Failed to fetch stats"), {
      status: res.status,
    });
  return res.json() as Promise<StatsOut>;
}

async function fetchConversations(): Promise<{ items: ConversationItem[] }> {
  const res = await fetch("/api/v1/conversations?limit=20");
  if (!res.ok)
    throw Object.assign(new Error("Failed to fetch conversations"), {
      status: res.status,
    });
  return res.json() as Promise<{ items: ConversationItem[] }>;
}

function truncate(text: string, max: number) {
  return text.length > max ? text.slice(0, max) + "…" : text;
}

export default function DashboardPage() {
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: fetchStats,
  });

  const { data: conversationsData } = useQuery({
    queryKey: ["conversations"],
    queryFn: fetchConversations,
  });

  const answerRate =
    stats && stats.conversation_count > 0
      ? `${Math.round((stats.answered_count / stats.conversation_count) * 100)}%`
      : "n/a";

  const recentItems = (conversationsData?.items ?? []).slice(0, 5);

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold">Dashboard</h1>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Documents" value={stats?.document_count ?? "—"} />
        <StatCard label="Indexed" value={stats?.indexed_count ?? "—"} />
        <StatCard
          label="Conversations"
          value={stats?.conversation_count ?? "—"}
        />
        <StatCard label="Answer rate" value={answerRate} />
      </div>

      <section>
        <h2 className="mb-3 text-sm font-medium text-[hsl(var(--muted-foreground))]">
          Recent activity
        </h2>
        <div className="rounded-lg border border-[hsl(var(--border))]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[hsl(var(--border))] bg-[hsl(var(--card))]">
                <th className="px-4 py-2 text-left font-medium">Question</th>
                <th className="px-4 py-2 text-left font-medium">Status</th>
                <th className="px-4 py-2 text-left font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {recentItems.length === 0 ? (
                <tr>
                  <td
                    colSpan={3}
                    className="px-4 py-6 text-center text-[hsl(var(--muted-foreground))]"
                  >
                    No conversations yet.
                  </td>
                </tr>
              ) : (
                recentItems.map((item) => (
                  <tr
                    key={item.id}
                    className="border-b border-[hsl(var(--border))] last:border-0"
                  >
                    <td className="px-4 py-2">{truncate(item.question, 80)}</td>
                    <td className="px-4 py-2 capitalize">{item.status}</td>
                    <td className="px-4 py-2 text-[hsl(var(--muted-foreground))]">
                      {new Date(item.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
