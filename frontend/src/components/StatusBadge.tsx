import { Badge } from "@/components/ui/badge";

interface StatusBadgeProps {
  indexed: boolean;
  pii_blocked: boolean;
}

export function StatusBadge({ indexed, pii_blocked }: StatusBadgeProps) {
  if (pii_blocked) {
    return <Badge variant="destructive">PII detected</Badge>;
  }
  if (indexed) {
    return <Badge variant="default">Indexed</Badge>;
  }
  return <Badge variant="secondary">Pending</Badge>;
}
