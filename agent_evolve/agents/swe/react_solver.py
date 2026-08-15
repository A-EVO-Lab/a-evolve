"""Standalone ReAct solver for SWE-bench.

Same ReAct loop as terminal bench, but adapted for code patching:
- Tool: bash (execute in /testbed), submit (capture git diff)
- Observation: command output from Docker container
- Submit captures git diff as the patch
"""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert software engineer tasked with resolving GitHub issues by \
producing code patches.

## Approach

1. **Understand the issue**: Read the issue description carefully. Identify the root cause.
2. **Locate relevant code**: Use search tools (grep, find) to find the files and functions involved.
3. **Plan the fix**: Think step-by-step about what needs to change and why.
4. **Implement the fix**: Make minimal, precise edits. Avoid unnecessary changes.
5. **Verify**: Run existing tests to confirm the fix works and doesn't break anything.

## Guidelines

- Prefer small, focused patches over large rewrites.
- Always check for edge cases the issue description mentions.
- If the issue includes a reproduction script, use it to verify your fix.
- When in doubt, look at how similar patterns are handled elsewhere in the codebase.
- The repository is at /testbed. You are root inside a Docker container.

When you have completed the fix, call the `submit()` tool to finalize your patch."""

CONTINUE_PROMPT = (
    "Please proceed to the next step using your best judgement.\n"
    "If you believe you have completed the fix, please call the "
    "`submit()` tool to finalize your patch."
)


# ── Tool definitions (Bedrock Converse format) ────────────────────────

TOOL_SPECS = [
    {
        "toolSpec": {
            "name": "bash",
            "description": (
                "Execute a bash command inside the repository container. "
                "The repository is at /testbed. Use this to explore code, "
                "edit files, run tests, and generate patches. "
                "Each call is independent — no shell state is preserved between calls. "
                "Chain commands with && if you need sequential execution."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "cmd": {
                            "type": "string",
                            "description": "The bash command to execute.",
                        }
                    },
                    "required": ["cmd"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "submit",
            "description": (
                "Submit your fix. This captures the git diff of your changes "
                "as the final patch. Call this when you are confident the fix "
                "is correct and tests pass."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "confirmation": {
                            "type": "string",
                            "description": "Confirmation message (e.g. 'done').",
                        }
                    },
                    "required": ["confirmation"],
                }
            },
        }
    },
]

READ_SKILL_SPEC = {
    "toolSpec": {
        "name": "read_skill",
        "description": (
            "Read the full content of a skill by name. "
            "Use this to load detailed guidance for a skill listed in your system prompt."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name to read.",
                    }
                },
                "required": ["name"],
            }
        },
    }
}


# ── Tool executors ────────────────────────────────────────────────────

def _exec_bash(container_name: str, cmd: str, log: logging.Logger) -> str:
    """Execute bash command in the SWE-bench container."""
    cmd_preview = cmd[:200] + ("..." if len(cmd) > 200 else "")
    log.info("[bash] $ %s", cmd_preview)
    t0 = time.time()
    try:
        docker_cmd = [
            "docker", "exec", "-w", "/testbed",
            container_name, "bash", "--login", "-c", cmd,
        ]
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=300)
        output = ""
        if result.stderr:
            output = f"{result.stderr}\n"
        output = f"{output}{result.stdout}"
        if not output.strip():
            output = "(no output)"
        if len(output) > 15000:
            output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        elapsed = time.time() - t0
        log.info("[bash] done (%.1fs, %d chars)", elapsed, len(output))
        return output
    except subprocess.TimeoutExpired:
        log.warning("[bash] TIMEOUT after 300s")
        return "ERROR: Command timed out after 300 seconds."
    except Exception as e:
        log.error("[bash] ERROR: %s", e)
        return f"ERROR: {e}"


def _exec_submit(container_name: str, log: logging.Logger) -> str:
    """Capture git diff as the final patch."""
    log.info("[submit] Capturing git diff...")
    try:
        result = subprocess.run(
            ["docker", "exec", "-w", "/testbed", container_name,
             "git", "diff"],
            capture_output=True, text=True, timeout=30,
        )
        patch = result.stdout or ""
        if not patch.strip():
            log.warning("[submit] Empty git diff")
            return "(empty patch — no changes detected)"
        log.info("[submit] Captured patch (%d chars)", len(patch))
        return patch
    except Exception as e:
        log.error("[submit] ERROR capturing diff: %s", e)
        return f"ERROR: {e}"


# ── Conversation extraction ───────────────────────────────────────────

def extract_conversation(messages: list[dict]) -> list[dict]:
    """Convert Bedrock Converse messages to standardized format."""
    conv = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content_blocks = msg.get("content", [])
        entry = {"role": role, "parts": []}

        for block in content_blocks:
            if "text" in block:
                entry["parts"].append({"type": "text", "text": block["text"]})
            elif "toolUse" in block:
                tu = block["toolUse"]
                entry["parts"].append({
                    "type": "tool_use",
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                    "id": tu.get("toolUseId", ""),
                })
            elif "toolResult" in block:
                tr = block["toolResult"]
                result_text = ""
                for c in tr.get("content", []):
                    if "text" in c:
                        text = c["text"]
                        if len(text) > 3000:
                            text = text[:1500] + "\n...[truncated]...\n" + text[-1500:]
                        result_text += text + "\n"
                entry["parts"].append({
                    "type": "tool_result",
                    "text": result_text.strip(),
                    "id": tr.get("toolUseId", ""),
                })

        conv.append(entry)
    return conv


# ── The ReAct loop ────────────────────────────────────────────────────

class ReactSolverResult:
    """Result from the ReAct solver."""

    def __init__(self):
        self.messages: list[dict] = []
        self.submitted: bool = False
        self.patch: str = ""
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.tool_call_count: int = 0
        self.timed_out: bool = False


def react_solve(
    task_prompt: str,
    container_name: str,
    model_id: str = "us.anthropic.claude-opus-4-6-v1",
    region: str = "us-west-2",
    max_tokens: int = 16384,
    timeout_sec: int = 1800,
    max_turns: int = 200,
    log: logging.Logger | None = None,
    system_prompt: str | None = None,
    skills: dict[str, str] | None = None,
) -> ReactSolverResult:
    """Run the ReAct loop to solve a SWE-bench task.

    Args:
        task_prompt: The issue description (user message).
        container_name: Docker container name for tool execution.
        model_id: Bedrock model ID.
        region: AWS region.
        max_tokens: Max tokens per LLM call.
        timeout_sec: Wall-clock timeout for the entire solve.
        max_turns: Safety limit on LLM calls.
        log: Logger instance.
        system_prompt: Override default system prompt (with skills).
        skills: Dict of skill_name -> content for read_skill tool.

    Returns:
        ReactSolverResult with messages, patch, and usage.
    """
    import boto3
    from botocore.config import Config as BotoConfig

    if log is None:
        log = logger

    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=BotoConfig(read_timeout=300, retries={"max_attempts": 0}),
    )
    result = ReactSolverResult()

    # Build tool config
    all_specs = list(TOOL_SPECS) + ([READ_SKILL_SPEC] if skills else [])
    tool_config = {"tools": all_specs}

    # System prompt
    system_blocks = [{"text": system_prompt or SYSTEM_PROMPT}]

    # Build first user message
    messages = [{"role": "user", "content": [{"text": task_prompt}]}]
    result.messages = messages

    t0 = time.time()
    consecutive_errors = 0
    retry_lost_time = 0

    for turn in range(max_turns):
        # Check timeout
        effective_elapsed = time.time() - t0 - retry_lost_time
        if effective_elapsed >= timeout_sec:
            log.warning("Timeout reached (%.0fs effective >= %ds)",
                        effective_elapsed, timeout_sec)
            result.timed_out = True
            break

        # Call LLM
        log.debug("[turn %d] Calling LLM (%.0fs elapsed, %.0fs effective)...",
                  turn + 1, time.time() - t0, effective_elapsed)
        call_start = time.time()
        try:
            response = client.converse(
                modelId=model_id,
                messages=messages,
                system=system_blocks,
                inferenceConfig={"maxTokens": max_tokens},
                toolConfig=tool_config,
            )
            consecutive_errors = 0
        except Exception as e:
            err_str = str(e)
            if any(kw in err_str for kw in [
                "ThrottlingException", "internalServerException",
                "ServiceUnavailableException", "ModelTimeoutException",
                "Read timeout", "ConnectTimeoutError", "EndpointConnectionError",
                "content filtering policy",
            ]):
                consecutive_errors += 1
                wait = min(120, 15 * consecutive_errors)
                retry_lost_time += time.time() - call_start + wait
                wall_elapsed = time.time() - t0 + wait
                if consecutive_errors >= 5 or wall_elapsed >= timeout_sec * 2.5:
                    log.error("Giving up after %d retries: %s",
                              consecutive_errors, err_str[:200])
                    break
                log.warning("Transient error (%d/5): %s. Retrying in %ds...",
                            consecutive_errors, err_str[:150], wait)
                time.sleep(wait)
                continue
            else:
                log.error("LLM error: %s", err_str[:300])
                break

        # Track usage
        usage = response.get("usage", {})
        result.total_input_tokens += usage.get("inputTokens", 0)
        result.total_output_tokens += usage.get("outputTokens", 0)

        # Parse response
        output_msg = response.get("output", {}).get("message", {})
        content_blocks = output_msg.get("content", [])
        messages.append({"role": "assistant", "content": content_blocks})

        text_blocks = [b for b in content_blocks if "text" in b]
        tool_use_blocks = [b for b in content_blocks if "toolUse" in b]

        if text_blocks:
            log.debug("[turn %d] Assistant: %s",
                      turn + 1, text_blocks[0]["text"][:200])

        # Handle tool calls
        if tool_use_blocks:
            tool_results = []
            submitted = False

            for tu_block in tool_use_blocks:
                if time.time() - t0 - retry_lost_time >= timeout_sec:
                    result.timed_out = True
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tu_block["toolUse"]["toolUseId"],
                            "content": [{"text": "ERROR: Agent timeout reached."}],
                            "status": "error",
                        }
                    })
                    continue

                tu = tu_block["toolUse"]
                tool_name = tu["name"]
                tool_input = tu.get("input", {})
                tool_use_id = tu["toolUseId"]
                result.tool_call_count += 1

                if tool_name == "bash":
                    tool_output = _exec_bash(
                        container_name, tool_input.get("cmd", ""), log,
                    )
                elif tool_name == "submit":
                    tool_output = _exec_submit(container_name, log)
                    submitted = True
                    result.submitted = True
                    result.patch = tool_output
                elif tool_name == "read_skill":
                    skill_name = tool_input.get("name", "")
                    if skills and skill_name in skills:
                        tool_output = skills[skill_name]
                        log.info("[read_skill] %s (%d chars)",
                                 skill_name, len(tool_output))
                    else:
                        available = ", ".join(skills.keys()) if skills else "none"
                        tool_output = (
                            f"Skill '{skill_name}' not found. Available: {available}"
                        )
                        log.warning("[read_skill] not found: %s", skill_name)
                else:
                    tool_output = f"ERROR: Unknown tool '{tool_name}'"
                    log.warning("Unknown tool: %s", tool_name)

                tool_results.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": str(tool_output)}],
                    }
                })

            messages.append({"role": "user", "content": tool_results})

            if submitted:
                log.info("Agent submitted after %d turns, %.0fs",
                         turn + 1, time.time() - t0)
                break
            if result.timed_out:
                break

        else:
            # No tool calls — send continue prompt
            if time.time() - t0 - retry_lost_time >= timeout_sec:
                result.timed_out = True
                break
            log.info("[turn %d] No tool calls — sending continue prompt",
                     turn + 1)
            messages.append({"role": "user", "content": [{"text": CONTINUE_PROMPT}]})

    # If not submitted, try to capture the diff anyway
    if not result.submitted:
        try:
            diff_result = subprocess.run(
                ["docker", "exec", "-w", "/testbed", container_name, "git", "diff"],
                capture_output=True, text=True, timeout=30,
            )
            result.patch = diff_result.stdout or ""
            if result.patch.strip():
                log.info("Captured unsolicited patch (%d chars)", len(result.patch))
        except Exception:
            pass

    elapsed = time.time() - t0
    log.info(
        "ReAct loop done: %d turns, %d tool calls, %.0fs, "
        "tokens=%d in + %d out, submitted=%s, patch=%d chars",
        turn + 1, result.tool_call_count, elapsed,
        result.total_input_tokens, result.total_output_tokens,
        result.submitted, len(result.patch),
    )
    result.messages = messages
    return result
