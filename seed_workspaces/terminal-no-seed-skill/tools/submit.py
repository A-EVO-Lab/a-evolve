"""Submit tool executor for Terminal-Bench 2.0."""


def execute(container_name: str, tool_input: dict, log) -> str:
    """Handle submit tool call."""
    answer = tool_input.get("answer", "")
    log.info("[submit] %s", answer)
    return answer
