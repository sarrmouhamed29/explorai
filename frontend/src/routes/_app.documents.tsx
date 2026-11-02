import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";

import { DeleteDialog } from "@/components/DeleteDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { UploadDialog } from "@/components/UploadDialog";

export const Route = createFileRoute("/_app/documents")({
  component: DocumentsPage,
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

async function fetchDocuments(): Promise<{ items: DocumentOut[] }> {
  const res = await fetch("/api/v1/documents?scope=mine&limit=50");
  if (!res.ok) throw new Error("Failed to fetch documents");
  return res.json() as Promise<{ items: DocumentOut[] }>;
}

export default function DocumentsPage() {
  const { data } = useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Documents</h1>
        <UploadDialog />
      </div>

      <div className="rounded-lg border border-[hsl(var(--border))]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[hsl(var(--border))] bg-[hsl(var(--card))]">
              <th className="px-4 py-2 text-left font-medium">Title</th>
              <th className="px-4 py-2 text-left font-medium">Status</th>
              <th className="px-4 py-2 text-left font-medium">Uploaded</th>
              <th className="px-4 py-2 text-left font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td
                  colSpan={4}
                  className="px-4 py-6 text-center text-[hsl(var(--muted-foreground))]"
                >
                  No documents yet. Upload a PDF to get started.
                </td>
              </tr>
            ) : (
              items.map((doc) => (
                <tr
                  key={doc.id}
                  className="border-b border-[hsl(var(--border))] last:border-0"
                >
                  <td className="px-4 py-2">
                    <Link
                      to="/documents/$documentId"
                      params={{ documentId: doc.id }}
                      className="font-medium text-[hsl(var(--primary))] hover:underline"
                    >
                      {doc.title ?? doc.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge
                      indexed={doc.indexed}
                      pii_blocked={doc.pii_blocked}
                    />
                  </td>
                  <td className="px-4 py-2 text-[hsl(var(--muted-foreground))]">
                    {new Date(doc.created_at).toLocaleDateString()}
                  </td>
                  <td className="flex items-center gap-2 px-4 py-2">
                    {!doc.pii_blocked && (
                      <Link
                        to="/chat"
                        search={{ documentId: doc.id }}
                        className="text-xs text-[hsl(var(--primary))] hover:underline"
                      >
                        Ask
                      </Link>
                    )}
                    {doc.owned && (
                      <DeleteDialog
                        documentId={doc.id}
                        title={doc.title ?? doc.id}
                      />
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
