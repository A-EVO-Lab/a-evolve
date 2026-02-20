import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("json", json);

export function CodeViewer({
  title,
  content,
  language = "python"
}: {
  title: string;
  content: string | null | undefined;
  language?: "python" | "markdown" | "json";
}) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
      <div className="mb-2 text-sm font-semibold">{title}</div>
      <SyntaxHighlighter
        language={language}
        style={vscDarkPlus}
        customStyle={{
          margin: 0,
          maxHeight: "20rem",
          overflow: "auto",
          background: "#0b1020",
          fontSize: "12px",
          padding: "12px",
          borderRadius: "8px"
        }}
        wrapLongLines
      >
        {content ?? "No content"}
      </SyntaxHighlighter>
    </div>
  );
}
