import { useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

interface DocumentOut {
  id: string;
  title: string | null;
}

interface ChatInputBarProps {
  onSend: (question: string, scope: string, documentId?: string) => void;
  isLoading: boolean;
  initialDocumentId?: string;
}

export function ChatInputBar({
  onSend,
  isLoading,
  initialDocumentId,
}: ChatInputBarProps) {
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState("mine");
  const [docId, setDocId] = useState(initialDocumentId ?? "");

  const { data } = useQuery<{ items: DocumentOut[] }>({
    queryKey: ["documents"],
    queryFn: async () => {
      const res = await fetch("/api/v1/documents?scope=mine&limit=50");
      if (!res.ok) throw new Error("Failed");
      return res.json() as Promise<{ items: DocumentOut[] }>;
    },
  });

  const docs = data?.items ?? [];

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || isLoading) return;
    onSend(question.trim(), scope, docId || undefined);
    setQuestion("");
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value.slice(0, 2000))}
        placeholder="Ask a question about your documents…"
        className="w-full resize-none rounded-md border border-[hsl(var(--border))] p-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
        rows={3}
      />
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="chat-scope" className="text-sm">
          Scope
        </label>
        <select
          id="chat-scope"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          className="rounded border border-[hsl(var(--border))] px-2 py-1 text-sm"
        >
          <option value="mine">My documents</option>
          <option value="all">All documents</option>
        </select>
        <select
          aria-label="Document"
          value={docId}
          onChange={(e) => setDocId(e.target.value)}
          className="flex-1 rounded border border-[hsl(var(--border))] px-2 py-1 text-sm"
        >
          <option value="">All documents</option>
          {docs.map((d) => (
            <option key={d.id} value={d.id}>
              {d.title ?? d.id}
            </option>
          ))}
        </select>
        <Button type="submit" size="sm" disabled={isLoading}>
          {isLoading ? (
            <span className="mr-1 inline-block animate-spin">⟳</span>
          ) : (
            <Send className="mr-1 h-4 w-4" />
          )}
          Send
        </Button>
      </div>
    </form>
  );
}
