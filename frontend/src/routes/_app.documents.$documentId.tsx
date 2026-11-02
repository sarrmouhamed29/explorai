import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useParams } from "@tanstack/react-router";

import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/_app/documents/$documentId")({
  component: DocumentDetailPage,
});

interface DocumentOut {
  id: string;
  title: string | null;
  indexed: boolean;
  pii_blocked: boolean;
  source_kind: string;
  owned: boolean;
  status: string;
  created_at: string;
}

interface SectionItem {
  ordinal: number;
  heading: string | null;
  text: string;
}

export default function DocumentDetailPage() {
  const { documentId } = useParams({
    from: "/_app/documents/$documentId",
    strict: false,
  }) as {
    documentId: string;
  };

  const { data: doc } = useQuery<DocumentOut>({
    queryKey: ["document", documentId],
    queryFn: async () => {
      const res = await fetch(`/api/v1/documents/${documentId}`);
      if (!res.ok) throw new Error("Not found");
      return res.json() as Promise<DocumentOut>;
    },
    enabled: !!documentId,
  });

  const { data: sectionsData } = useQuery<{ items: SectionItem[] }>({
    queryKey: ["document", documentId, "sections"],
    queryFn: async () => {
      const res = await fetch(`/api/v1/documents/${documentId}/sections`);
      if (!res.ok) throw new Error("Not found");
      return res.json() as Promise<{ items: SectionItem[] }>;
    },
    enabled: !!documentId,
  });

  const sections = sectionsData?.items ?? [];

  if (!doc) {
    return <p className="text-[hsl(var(--muted-foreground))]">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">{doc.title ?? doc.id}</h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
            <StatusBadge indexed={doc.indexed} pii_blocked={doc.pii_blocked} />
            <span>{new Date(doc.created_at).toLocaleDateString()}</span>
          </div>
        </div>
        {!doc.pii_blocked && (
          <Button asChild size="sm">
            <Link to="/chat" search={{ documentId: doc.id }}>
              Ask about this document
            </Link>
          </Button>
        )}
      </div>

      <div className="space-y-4">
        {sections.length === 0 ? (
          <p className="text-[hsl(var(--muted-foreground))]">
            No sections extracted yet.
          </p>
        ) : (
          sections.map((s) => (
            <div
              key={s.ordinal}
              className="rounded-lg border border-[hsl(var(--border))] p-4"
            >
              <p className="mb-1 text-xs font-semibold uppercase text-[hsl(var(--muted-foreground))]">
                {s.ordinal}.{s.heading && ` ${s.heading}`}
              </p>
              <p className="text-sm leading-relaxed">{s.text}</p>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
