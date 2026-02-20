import { useState } from "react";

export function LiveLog({ lines }: { lines: string[] }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm font-semibold">Live Log</div>
        <button
          type="button"
          className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs"
          onClick={() => setExpanded((prev) => !prev)}
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>
      {expanded ? (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-xs text-slate-200">
          {lines.length > 0 ? lines.join("\n") : "No logs yet."}
        </pre>
      ) : (
        <div className="text-xs text-slate-400">Click Expand to view live logs.</div>
      )}
    </div>
  );
}
