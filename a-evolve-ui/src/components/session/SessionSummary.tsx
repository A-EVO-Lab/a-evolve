import { Badge } from "@/components/ui/Badge";
import type { SessionSummary as SessionSummaryType } from "@/types/session";

export function SessionSummary({
  session
}: {
  session: SessionSummaryType | null;
}) {
  if (!session) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-300">
        No session selected
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold text-slate-100">{session.id}</div>
      <div className="flex flex-wrap gap-2">
        <Badge tone="info">Batches {session.counts.batches}</Badge>
        <Badge>Tools {session.counts.tools}</Badge>
        <Badge>Skills {session.counts.skills}</Badge>
        <Badge>Knowledge {session.counts.knowledge}</Badge>
      </div>
    </div>
  );
}
