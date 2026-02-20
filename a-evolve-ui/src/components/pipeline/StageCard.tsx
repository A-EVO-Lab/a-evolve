import { Badge } from "@/components/ui/Badge";
import type { StageHistoryItem } from "@/types/evolve";

export function StageCard({ item }: { item: StageHistoryItem }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-1 flex items-center gap-2">
        <div className="text-sm font-semibold">
          Stage {item.stage} - {item.name}
        </div>
        <Badge tone="info">{item.name}</Badge>
      </div>
      <pre className="max-h-44 overflow-auto whitespace-pre-wrap text-xs text-slate-300">
        {JSON.stringify(item.result, null, 2)}
      </pre>
    </div>
  );
}
