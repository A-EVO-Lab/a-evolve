import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import type { ReactNode } from "react";

export function TwoPanelLayout({
  left,
  right
}: {
  left: ReactNode;
  right: ReactNode;
}) {
  return (
    <PanelGroup direction="horizontal" className="h-full">
      <Panel minSize={20} defaultSize={30} className="overflow-hidden">
        {left}
      </Panel>
      <PanelResizeHandle className="w-px bg-slate-700" />
      <Panel minSize={40} defaultSize={70} className="overflow-hidden">
        {right}
      </Panel>
    </PanelGroup>
  );
}
