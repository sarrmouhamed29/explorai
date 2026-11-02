import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { ChatInputBar } from "@/components/ChatInputBar";
import { CitationBadge } from "@/components/CitationBadge";
import { ConversationHistory } from "@/components/ConversationHistory";
import { InsufficientEvidenceCallout } from "@/components/InsufficientEvidenceCallout";
import { Alert, AlertDescription } from "@/components/ui/alert";

export const Route = createFileRoute("/_app/chat")({
  validateSearch: (search: Record<string, unknown>) => ({
    documentId:
      typeof search.documentId === "string" ? search.documentId : undefined,
  }),
  component: ChatPageRoute,
});

interface CitationOut {
  source: number;
  quote: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  section: string | null;
}

interface AskResponse {
  status: "answered" | "insufficient_evidence" | "pii_blocked" | "error";
  answer: string | null;
  citations: CitationOut[];
  usage: { input_tokens: number; output_tokens: number };
}

interface ConversationOut {
  id: string;
  question: string;
  status: string;
  created_at: string;
}

interface ChatPageProps {
  searchParams?: { documentId?: string };
}

function ChatPageRoute() {
  const routeSearch = useSearch({ from: "/_app/chat" }) as {
    documentId?: string;
  };
  return <ChatPage searchParams={routeSearch} />;
}

export default function ChatPage({ searchParams }: ChatPageProps = {}) {
  const queryClient = useQueryClient();
  const [exchange, setExchange] = useState<{
    question: string;
    response: AskResponse;
  } | null>(null);
  const [rateLimitError, setRateLimitError] = useState(false);

  const { data: conversationsData } = useQuery<{ items: ConversationOut[] }>({
    queryKey: ["conversations"],
    queryFn: async () => {
      const res = await fetch("/api/v1/conversations?limit=20");
      if (!res.ok)
        throw Object.assign(new Error("Failed"), { status: res.status });
      return res.json() as Promise<{ items: ConversationOut[] }>;
    },
  });

  const conversations = conversationsData?.items ?? [];

  const mutation = useMutation({
    mutationFn: async ({
      question,
      scope,
      documentId,
    }: {
      question: string;
      scope: string;
      documentId?: string;
    }) => {
      const res = await fetch("/api/v1/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          scope,
          document_id: documentId ?? null,
        }),
      });
      if (res.status === 429)
        throw Object.assign(new Error("Rate limit"), { status: 429 });
      if (!res.ok) throw new Error("Ask failed");
      return res.json() as Promise<AskResponse>;
    },
    onSuccess: (data, variables) => {
      setRateLimitError(false);
      setExchange({ question: variables.question, response: data });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (error: Error & { status?: number }) => {
      if (error.status === 429) setRateLimitError(true);
    },
  });

  return (
    <div className="flex h-full gap-6">
      <aside className="w-64 shrink-0">
        <h2 className="mb-3 text-sm font-semibold">History</h2>
        <ConversationHistory
          conversations={conversations}
          onSelect={() => {}}
        />
      </aside>

      <div className="flex flex-1 flex-col gap-4">
        <div className="flex-1 space-y-4 overflow-y-auto">
          {rateLimitError && (
            <Alert variant="destructive">
              <AlertDescription>
                Rate limit reached. Please try again later.
              </AlertDescription>
            </Alert>
          )}
          {exchange && (
            <div className="space-y-4">
              <p className="font-medium">{exchange.question}</p>
              {exchange.response.status === "insufficient_evidence" ? (
                <InsufficientEvidenceCallout />
              ) : (
                <div>
                  <p>{exchange.response.answer}</p>
                  {exchange.response.citations.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {exchange.response.citations.map((c) => (
                        <CitationBadge
                          key={c.chunk_id}
                          citation={c}
                          index={c.source}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <ChatInputBar
          onSend={(question, scope, documentId) =>
            mutation.mutate({ question, scope, documentId })
          }
          isLoading={mutation.isPending}
          initialDocumentId={searchParams?.documentId}
        />
      </div>
    </div>
  );
}
