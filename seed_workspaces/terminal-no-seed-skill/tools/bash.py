"""Bash tool executor for Terminal-Bench 2.0."""

import subprocess
import time


def execute(container_name: str, tool_input: dict, log) -> str:
    """Execute a bash command in the Docker container."""
    cmd = tool_input.get("cmd", "")
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    elif not isinstance(cmd, str):
        cmd = str(cmd)
    cmd_preview = cmd[:200] + ("..." if len(cmd) > 200 else "")
    log.info("[bash] $ %s", cmd_preview)
    t0 = time.time()
    try:
        docker_cmd = ["docker", "exec", container_name, "bash", "--login", "-c", cmd]
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=60)
        output = ""
        if result.stderr:
            output = f"{result.stderr}\n"
        output = f"{output}{result.stdout}"
        if not output.strip():
            output = "(no output)"
        if len(output) > 15000:
            output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
        elapsed = time.time() - t0
        log.info("[bash] done (%.1fs, %d chars)", elapsed, len(output))
        return output
    except subprocess.TimeoutExpired:
        log.warning("[bash] TIMEOUT after 60s")
        return "ERROR: Command timed out after 60 seconds."
    except Exception as e:
        log.error("[bash] ERROR: %s", e)
        return f"ERROR: {e}"
