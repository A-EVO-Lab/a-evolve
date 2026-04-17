#!/usr/bin/env python3
"""
Group CL-bench tasks by context_id.

Same context_id tasks share system prompt and context.
Context/Task split: multi-task uses longest-common-prefix; single-task uses
last-paragraph split (last paragraph = task, preceding = context).
Procedural Task Execution category preserves original row order.

Output format (one JSON per line):
{
  "context_id": "...",
  "context_category": "...",
  "sub_category": "...",
  "system_prompt": "...",
  "context": "...",
  "tasks": [
    {"task_id": "...", "task": "...", "rubrics": [...], "order": 0},
    ...
  ]
}

Usage:
    python group_by_context.py --input CL-bench.jsonl --output CL-bench-grouped.jsonl
"""

import argparse
import json
import os
from collections import defaultdict


def load_jsonl(path, max_samples=None):
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if max_samples and i >= max_samples:
                break
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def _longest_common_prefix(strings: list) -> str:
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _split_by_last_paragraph(content: str) -> tuple[str, str]:
    if not content or not content.strip():
        return "", content
    parts = content.split("\n\n")
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return "", content.strip()
    task = parts[-1]
    context = "\n\n".join(parts[:-1])
    return context, task


def get_user_content(msg):
    c = msg.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(b.get("text", b)) for b in c) if c else ""
    return str(c)


def main():
    p = argparse.ArgumentParser(description="Group CL-bench by context_id")
    p.add_argument("--input", default="CL-bench.jsonl")
    p.add_argument("--output", default="CL-bench-grouped.jsonl")
    p.add_argument("--max-samples", type=int, default=None)
    args = p.parse_args()

    data = load_jsonl(args.input, args.max_samples)
    if not data:
        print("No data loaded.")
        return 1

    by_ctx = defaultdict(list)
    for row_idx, d in enumerate(data):
        ctx_id = d.get("metadata", {}).get("context_id", "")
        if not ctx_id:
            ctx_id = f"_no_ctx_{row_idx}"
        by_ctx[ctx_id].append((row_idx, d))

    grouped = []
    for ctx_id, items in sorted(by_ctx.items(), key=lambda x: min(i[0] for i in x[1])):
        items = sorted(items, key=lambda x: x[0])
        row_idx0, d0 = items[0]
        meta = d0.get("metadata", {})
        context_category = meta.get("context_category", "")
        sub_category = meta.get("sub_category", "")

        msgs0 = d0.get("messages", [])
        system_content = ""
        for m in msgs0:
            if m.get("role") == "system":
                system_content = get_user_content(m)
                break

        first_user_contents = []
        for row_idx, d in items:
            msgs = d.get("messages", [])
            for m in msgs:
                if m.get("role") == "user":
                    first_user_contents.append(get_user_content(m))
                    break
            else:
                first_user_contents.append("")

        if len(first_user_contents) == 1:
            context_part, task0_part = _split_by_last_paragraph(first_user_contents[0])
        else:
            common_prefix = _longest_common_prefix(first_user_contents)
            if common_prefix and not common_prefix.endswith("\n"):
                last_nl = common_prefix.rfind("\n")
                if last_nl > 0:
                    common_prefix = common_prefix[: last_nl + 1]
            context_part = common_prefix.rstrip()
            task0_part = ""

        tasks = []
        for idx, (row_idx, d) in enumerate(items):
            user_content = first_user_contents[idx] if idx < len(first_user_contents) else ""
            if len(items) == 1:
                task_part = task0_part
            else:
                prefix = context_part
                if user_content.startswith(prefix):
                    task_part = user_content[len(prefix) :].strip()
                else:
                    task_part = user_content.strip()

            tasks.append({
                "task_id": d.get("metadata", {}).get("task_id", ""),
                "task": task_part,
                "rubrics": d.get("rubrics", []),
                "order": row_idx,
            })

        rec = {
            "context_id": ctx_id,
            "context_category": context_category,
            "sub_category": sub_category,
            "system_prompt": system_content,
            "context": context_part,
            "tasks": tasks,
        }
        grouped.append(rec)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for rec in grouped:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Written {len(grouped)} contexts to {args.output}")
    print(f"Total tasks: {sum(len(r['tasks']) for r in grouped)}")
    proc = sum(1 for r in grouped if "Procedural" in r.get("context_category", ""))
    print(f"Procedural Task Execution contexts: {proc}")
    return 0


if __name__ == "__main__":
    exit(main())
