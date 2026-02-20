import { useMemo, useState } from "react";
import { CodeViewer } from "@/components/artifacts/CodeViewer";

export function ToolInspector({
  tools
}: {
  tools: Array<Record<string, unknown>>;
}) {
  const [selected, setSelected] = useState(0);
  const selectedTool = tools[selected];
  const title = useMemo(
    () => String(selectedTool?.name ?? "Tool source"),
    [selectedTool]
  );

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="mb-2 text-sm font-semibold">Tools</div>
        <div className="space-y-1">
          {tools.map((tool, index) => (
            <button
              key={String(tool.id ?? index)}
              type="button"
              onClick={() => setSelected(index)}
              className={`block w-full rounded-md border px-2 py-1 text-left text-xs transition-colors duration-150 ${
                selected === index
                  ? "border-indigo-500 bg-indigo-500/10"
                  : "border-slate-700 bg-slate-950 hover:border-slate-600 hover:bg-slate-800/50"
              }`}
            >
              {String(tool.name ?? "unknown")}
            </button>
          ))}
        </div>
      </div>
      <CodeViewer
        title={title}
        content={(selectedTool?.source as string) ?? null}
        language="python"
      />
    </div>
  );
}
