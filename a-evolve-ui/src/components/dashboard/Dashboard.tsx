import { LearningCurveChart } from "@/components/dashboard/LearningCurveChart";
import { SummaryCards } from "@/components/dashboard/SummaryCards";
import type { BatchSummary, SessionSummary } from "@/types/session";

export function Dashboard({
  session,
  batches
}: {
  session: SessionSummary | null;
  batches?: BatchSummary[];
}) {
  return (
    <div className="space-y-3 p-3">
      <SummaryCards session={session} batches={batches} />
      <LearningCurveChart session={session} batches={batches} />
    </div>
  );
}
