import { BatchCard } from "@/components/batch/BatchCard";
import { SearchInput } from "@/components/ui/SearchInput";
import type { BatchSummary } from "@/types/session";
import { useMemo, useState } from "react";

export function BatchList({
  batches,
  selectedBatchId,
  onSelectBatch
}: {
  batches: BatchSummary[];
  selectedBatchId: string | null;
  onSelectBatch: (batchId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () => batches.filter((b) => b.id.toLowerCase().includes(query.toLowerCase())),
    [batches, query]
  );

  return (
    <div className="flex h-full flex-col border-r border-slate-700 bg-slate-950 p-3">
      <div className="mb-2 flex-shrink-0 text-sm font-semibold text-slate-100">Batches</div>
      <div className="flex-shrink-0">
        <SearchInput value={query} onChange={setQuery} placeholder="Search batch id" />
      </div>
      <div className="mt-3 min-h-0 flex-1 space-y-2 overflow-y-auto pb-8">
        {filtered.map((batch) => (
          <BatchCard
            key={batch.id}
            batch={batch}
            active={batch.id === selectedBatchId}
            onClick={() => onSelectBatch(batch.id)}
          />
        ))}
      </div>
    </div>
  );
}
