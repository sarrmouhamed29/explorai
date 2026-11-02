import { useState } from "react";

import { Badge } from "@/components/ui/badge";

interface CitationOut {
  source: number;
  quote: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  section: string | null;
}

interface CitationBadgeProps {
  citation: CitationOut;
  index: number;
}

export function CitationBadge({ citation, index }: CitationBadgeProps) {
  const [open, setOpen] = useState(false);

  return (
    <span className="relative inline-block">
      <Badge
        variant="secondary"
        className="cursor-pointer select-none"
        onClick={() => setOpen((p) => !p)}
        aria-label={`Citation ${index}`}
      >
        {index}
      </Badge>
      {open && (
        <div className="absolute bottom-full left-0 z-10 mb-1 w-64 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-xs shadow-lg">
          <p className="font-semibold">{citation.document_title}</p>
          <p className="mt-1 italic">&ldquo;{citation.quote}&rdquo;</p>
        </div>
      )}
    </span>
  );
}
