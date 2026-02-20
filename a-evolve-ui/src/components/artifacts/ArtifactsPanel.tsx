import { SkillInspector } from "@/components/artifacts/SkillInspector";
import { CodeViewer } from "@/components/artifacts/CodeViewer";
import { ToolInspector } from "@/components/artifacts/ToolInspector";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useState } from "react";

export function ArtifactsPanel({
  tools,
  skills,
  knowledge
}: {
  tools: Array<Record<string, unknown>>;
  skills: Array<{ name: string; path: string; content: string }>;
  knowledge: Array<Record<string, unknown>>;
}) {
  const [tab, setTab] = useState("tools");

  return (
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList className="mb-3 flex gap-2">
        <TabsTrigger
          value="tools"
          className="rounded-md border border-slate-700 px-2 py-1 text-xs transition-colors duration-150 data-[state=active]:border-indigo-500 data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-100 hover:bg-slate-800"
        >
          Tools
        </TabsTrigger>
        <TabsTrigger
          value="skills"
          className="rounded-md border border-slate-700 px-2 py-1 text-xs transition-colors duration-150 data-[state=active]:border-indigo-500 data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-100 hover:bg-slate-800"
        >
          Skills
        </TabsTrigger>
        <TabsTrigger
          value="knowledge"
          className="rounded-md border border-slate-700 px-2 py-1 text-xs transition-colors duration-150 data-[state=active]:border-indigo-500 data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-100 hover:bg-slate-800"
        >
          Knowledge
        </TabsTrigger>
      </TabsList>

      <TabsContent value="tools">
        <ToolInspector tools={tools} />
      </TabsContent>
      <TabsContent value="skills">
        <SkillInspector skills={skills} />
      </TabsContent>
      <TabsContent value="knowledge">
        <CodeViewer title="Knowledge" content={JSON.stringify(knowledge, null, 2)} language="json" />
      </TabsContent>
    </Tabs>
  );
}
