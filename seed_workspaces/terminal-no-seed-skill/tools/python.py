"""Python tool executor for Terminal-Bench 2.0."""

import subprocess
import time


def execute(container_name: str, tool_input: dict, log) -> str:
    """Execute Python code in the Docker container via stdin."""
    code = tool_input.get("code", "")
    if isinstance(code, list):
        code = "\n".join(str(x) for x in code)
    elif not isinstance(code, str):
        code = str(code)
    code_preview = code[:200] + ("..." if len(code) > 200 else "")
    log.info("[python] >>> %s", code_preview.replace("\n", "\\n"))
    t0 = time.time()
    try:
        docker_cmd = [
            "docker", "exec", "-i", container_name,
            "bash", "--login", "-c", "python3 -",
        ]
        result = subprocess.run(
            docker_cmd, capture_output=True, text=True, timeout=60, input=code,
        )
        output = ""
        if result.stderr:
            output = f"{result.stderr}\n"
        output = f"{output}{result.stdout}"
        if not output.strip():
            output = "(no output)"
        if len(output) > 15000:
            output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
        elapsed = time.time() - t0
        log.info("[python] done (%.1fs, %d chars)", elapsed, len(output))
        return output
    except subprocess.TimeoutExpired:
        log.warning("[python] TIMEOUT after 60s")
        return "ERROR: Command timed out after 60 seconds."
    except Exception as e:
        log.error("[python] ERROR: %s", e)
        return f"ERROR: {e}"
