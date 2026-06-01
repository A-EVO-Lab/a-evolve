"""strands tool wrappers for SkillBench container interaction.

Creates :class:`PythonAgentTool` instances that execute commands inside
a running :class:`SkillBenchContainer` via ``docker exec``.
"""

from __future__ import annotations

from typing import Any, Callable

from strands.tools.tools import PythonAgentTool, ToolSpec


def create_container_tools(
    get_container: Callable,
    on_skill_event: Callable[[dict[str, Any]], None] | None = None,
) -> list[PythonAgentTool]:
    """Return strands tools bound to *get_container()*.

    ``get_container`` is a zero-arg callable returning the active
    :class:`SkillBenchContainer`.  Using a callable (rather than the
    container directly) lets the agent build tools before the container
    is started.
    """
    return [
        _make_bash_tool(get_container),
        _make_python_tool(get_container),
        _make_read_file_tool(get_container),
        _make_write_file_tool(get_container),
        _make_list_skills_tool(get_container, on_skill_event=on_skill_event),
        _make_load_skill_tool(get_container, on_skill_event=on_skill_event),
    ]


# ── Individual tool factories ────────────────────────────────────────


def _make_bash_tool(get_container: Callable) -> PythonAgentTool:
    spec: ToolSpec = {
        "name": "bash",
        "description": (
            "Execute a bash command inside the task container and return "
            "its stdout+stderr.  Commands run as root in /root."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
            },
            "required": ["command"],
        },
    }

    def handler(tool_use: dict[str, Any], **kw: Any) -> dict[str, Any]:
        cmd = tool_use.get("input", {}).get("command", "")
        container = get_container()
        stdout, stderr, rc = container.exec_command(cmd)
        output = stdout + stderr
        if len(output) > 50_000:
            output = output[:25_000] + "\n...[truncated]...\n" + output[-25_000:]
        return _ok(tool_use, f"exit_code={rc}\n{output}")

    return PythonAgentTool("bash", spec, handler)


def _make_python_tool(get_container: Callable) -> PythonAgentTool:
    spec: ToolSpec = {
        "name": "python",
        "description": (
            "Execute a Python 3 script inside the task container. "
            "Provide the full script as a string."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute.",
                },
            },
            "required": ["code"],
        },
    }

    def handler(tool_use: dict[str, Any], **kw: Any) -> dict[str, Any]:
        code = tool_use.get("input", {}).get("code", "")
        container = get_container()
        container.write_file("/tmp/_run.py", code)
        stdout, stderr, rc = container.exec_command("python3 /tmp/_run.py")
        output = stdout + stderr
        if len(output) > 50_000:
            output = output[:25_000] + "\n...[truncated]...\n" + output[-25_000:]
        return _ok(tool_use, f"exit_code={rc}\n{output}")

    return PythonAgentTool("python", spec, handler)


def _make_read_file_tool(get_container: Callable) -> PythonAgentTool:
    spec: ToolSpec = {
        "name": "read_file",
        "description": "Read the contents of a file inside the task container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file.",
                },
            },
            "required": ["path"],
        },
    }

    def handler(tool_use: dict[str, Any], **kw: Any) -> dict[str, Any]:
        path = tool_use.get("input", {}).get("path", "")
        container = get_container()
        try:
            content = container.read_file(path)
            if len(content) > 100_000:
                content = content[:50_000] + "\n...[truncated]...\n" + content[-50_000:]
            return _ok(tool_use, content)
        except FileNotFoundError as e:
            return _ok(tool_use, f"Error: {e}")

    return PythonAgentTool("read_file", spec, handler)


def _make_write_file_tool(get_container: Callable) -> PythonAgentTool:
    spec: ToolSpec = {
        "name": "write_file",
        "description": (
            "Write content to a file inside the task container. "
            "Parent directories are created automatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path for the file.",
                },
                "content": {
                    "type": "string",
                    "description": "File content to write.",
                },
            },
            "required": ["path", "content"],
        },
    }

    def handler(tool_use: dict[str, Any], **kw: Any) -> dict[str, Any]:
        inp = tool_use.get("input", {})
        path = inp.get("path", "")
        content = inp.get("content", "")
        container = get_container()
        try:
            container.write_file(path, content)
            return _ok(tool_use, f"Wrote {len(content)} bytes to {path}")
        except RuntimeError as e:
            return _ok(tool_use, f"Error: {e}")

    return PythonAgentTool("write_file", spec, handler)


def _make_list_skills_tool(
    get_container: Callable,
    on_skill_event: Callable[[dict[str, Any]], None] | None = None,
) -> PythonAgentTool:
    SKILL_DIRS = [
        "/root/.agents/skills",
        "/root/.claude/skills",
        "/root/.codex/skills",
    ]
    spec: ToolSpec = {
        "name": "list_skills",
        "description": (
            "List available skills inside the container. "
            "Returns skill names and descriptions from SKILL.md frontmatter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    }

    def handler(tool_use: dict[str, Any], **kw: Any) -> dict[str, Any]:
        container = get_container()
        skills_found: list[str] = []
        skill_names: list[str] = []
        for sdir in SKILL_DIRS:
            stdout, _, rc = container.exec_command(
                f"ls -1 {sdir} 2>/dev/null", timeout=15,
            )
            if rc == 0 and stdout.strip():
                for name in stdout.strip().split("\n"):
                    name = name.strip()
                    if not name:
                        continue
                    skill_names.append(name)
                    desc_out, _, _ = container.exec_command(
                        f"head -10 {sdir}/{name}/SKILL.md 2>/dev/null"
                        " | grep -i 'description:' | head -1",
                        timeout=15,
                    )
                    desc = desc_out.strip().replace("description:", "").strip()
                    skills_found.append(f"- {name}: {desc or 'No description'}")
                if skills_found:
                    break
        if not skills_found:
            if on_skill_event:
                on_skill_event({
                    "tool": "list_skills",
                    "found": False,
                    "skills": [],
                })
            return _ok(tool_use, "No skills found in the container.")
        if on_skill_event:
            on_skill_event({
                "tool": "list_skills",
                "found": True,
                "skills": skill_names,
            })
        return _ok(tool_use, "Available skills:\n" + "\n".join(skills_found))

    return PythonAgentTool("list_skills", spec, handler)


def _make_load_skill_tool(
    get_container: Callable,
    on_skill_event: Callable[[dict[str, Any]], None] | None = None,
) -> PythonAgentTool:
    SKILL_DIRS = [
        "/root/.agents/skills",
        "/root/.claude/skills",
        "/root/.codex/skills",
    ]
    spec: ToolSpec = {
        "name": "load_skill",
        "description": (
            "Load and return the full SKILL.md content for a named skill. "
            "Use list_skills first to discover available skill names."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill folder name (e.g. 'xlsx', 'pid-control').",
                },
            },
            "required": ["name"],
        },
    }

    def handler(tool_use: dict[str, Any], **kw: Any) -> dict[str, Any]:
        skill_name = tool_use.get("input", {}).get("name", "")
        container = get_container()
        for sdir in SKILL_DIRS:
            try:
                content = container.read_file(f"{sdir}/{skill_name}/SKILL.md")
                if content.strip():
                    if on_skill_event:
                        on_skill_event({
                            "tool": "load_skill",
                            "name": skill_name,
                            "status": "loaded",
                            "source_dir": sdir,
                        })
                    return _ok(tool_use, content)
            except FileNotFoundError:
                continue
        if on_skill_event:
            on_skill_event({
                "tool": "load_skill",
                "name": skill_name,
                "status": "not_found",
            })
        return _ok(tool_use, f"Skill '{skill_name}' not found.")

    return PythonAgentTool("load_skill", spec, handler)


# ── Helpers ──────────────────────────────────────────────────────────


def _ok(tool_use: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "toolUseId": tool_use.get("toolUseId", "unknown"),
        "status": "success",
        "content": [{"text": text}],
    }
