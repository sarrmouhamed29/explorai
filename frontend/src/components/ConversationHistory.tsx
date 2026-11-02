interface ConversationOut {
  id: string;
  question: string;
  status: string;
  created_at: string;
}

interface ConversationHistoryProps {
  conversations: ConversationOut[];
  onSelect: (id: string) => void;
}

export function ConversationHistory({
  conversations,
  onSelect,
}: ConversationHistoryProps) {
  if (conversations.length === 0) {
    return (
      <p className="text-sm text-[hsl(var(--muted-foreground))]">
        No conversations yet.
      </p>
    );
  }

  return (
    <ul className="space-y-1">
      {conversations.map((c) => (
        <li key={c.id}>
          <button
            type="button"
            className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-[hsl(var(--muted))]"
            onClick={() => onSelect(c.id)}
          >
            <span className="block truncate font-medium">
              {c.question.slice(0, 60)}
              {c.question.length > 60 ? "…" : ""}
            </span>
            <span className="text-xs text-[hsl(var(--muted-foreground))]">
              {new Date(c.created_at).toLocaleDateString()}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
