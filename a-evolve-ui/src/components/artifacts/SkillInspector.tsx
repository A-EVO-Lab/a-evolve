import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("typescript", typescript);

export function SkillInspector({
  skills
}: {
  skills: Array<{ name: string; path: string; content: string }>;
}) {
  const [selected, setSelected] = useState(0);
  const selectedSkill = skills[selected];

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="mb-2 text-sm font-semibold">Skills</div>
        <div className="space-y-1">
          {skills.map((skill, index) => (
            <button
              key={skill.path}
              type="button"
              onClick={() => setSelected(index)}
              className={`block w-full rounded-md border px-2 py-1 text-left text-xs transition-colors duration-150 ${
                selected === index
                  ? "border-indigo-500 bg-indigo-500/10"
                  : "border-slate-700 bg-slate-950 hover:border-slate-600 hover:bg-slate-800/50"
              }`}
            >
              {skill.name}
            </button>
          ))}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-3">
        <div className="mb-2 text-sm font-semibold">{selectedSkill?.name ?? "Skill"}</div>
        <div className="max-h-80 overflow-auto text-sm text-slate-200">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({ children }) => (
                <h1 className="mb-3 border-b border-slate-700 pb-2 text-lg font-semibold text-slate-100">
                  {children}
                </h1>
              ),
              h2: ({ children }) => (
                <h2 className="mb-2 mt-4 text-base font-semibold text-slate-100">{children}</h2>
              ),
              h3: ({ children }) => (
                <h3 className="mb-2 mt-3 text-sm font-semibold text-slate-100">{children}</h3>
              ),
              p: ({ children }) => <p className="mb-3 leading-6 text-slate-200">{children}</p>,
              ul: ({ children }) => (
                <ul className="mb-3 list-disc space-y-1 pl-5 text-slate-200">{children}</ul>
              ),
              ol: ({ children }) => (
                <ol className="mb-3 list-decimal space-y-1 pl-5 text-slate-200">{children}</ol>
              ),
              li: ({ children }) => <li className="leading-6">{children}</li>,
              blockquote: ({ children }) => (
                <blockquote className="mb-3 border-l-2 border-indigo-500/50 pl-3 italic text-slate-300">
                  {children}
                </blockquote>
              ),
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="text-indigo-300 underline hover:text-indigo-200"
                >
                  {children}
                </a>
              ),
              code: ({ className, children }) => {
                const match = /language-(\w+)/.exec(className ?? "");
                const rawCode = String(children).replace(/\n$/, "");

                if (!match) {
                  return (
                    <code className="rounded bg-slate-800 px-1 py-0.5 font-mono text-xs text-indigo-200">
                      {rawCode}
                    </code>
                  );
                }

                return (
                  <SyntaxHighlighter
                    language={match[1]}
                    style={vscDarkPlus}
                    customStyle={{
                      margin: "0 0 12px 0",
                      borderRadius: "8px",
                      fontSize: "12px",
                      background: "#0b1020"
                    }}
                    wrapLongLines
                  >
                    {rawCode}
                  </SyntaxHighlighter>
                );
              }
            }}
          >
            {selectedSkill?.content ?? "No content"}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
