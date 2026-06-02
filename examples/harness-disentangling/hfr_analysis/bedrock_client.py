"""Bedrock Sonnet 4.6 wrapper for the SFR judge."""
from __future__ import annotations
import json
import os
import sys
import time
from typing import Any

import boto3

# Bring in EvolverBench's bedrock config + region resolver
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_evolverbench_root(start: str) -> str:
    """Walk up from `start` until `_region_picker.py` (EvolverBench root marker) is found."""
    d = start
    for _ in range(6):
        if os.path.exists(os.path.join(d, "_region_picker.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError(
        f"Could not locate EvolverBench root from {start} (no _region_picker.py within 6 parent levels)"
    )


EVOLVERBENCH_ROOT = _find_evolverbench_root(SCRIPT_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(EVOLVERBENCH_ROOT, "..", ".."))
sys.path.insert(0, EVOLVERBENCH_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from _region_picker import resolve  # type: ignore

try:
    from agent_evolve.llm._bedrock_config import bedrock_boto_config  # type: ignore
except ImportError:
    def _env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value >= 0 else default

    def bedrock_boto_config():
        from botocore.config import Config as BotoConfig

        return BotoConfig(
            retries={
                "max_attempts": _env_int("BEDROCK_RETRY_MAX_ATTEMPTS", 15),
                "mode": "adaptive",
            },
            read_timeout=_env_int("BEDROCK_READ_TIMEOUT_SEC", 600),
            connect_timeout=_env_int("BEDROCK_CONNECT_TIMEOUT_SEC", 30),
        )


JUDGE_MODEL_SHORT = "sonnet46"
DEFAULT_REGION = "us-west-2"


class SonnetJudge:
    """Thin Bedrock Converse API wrapper for Sonnet 4.6.

    - Honours BEDROCK_RETRY_MAX_ATTEMPTS / BEDROCK_READ_TIMEOUT_SEC env vars.
    - Returns parsed JSON dict (assumes prompt instructs JSON output).
    """

    def __init__(self, region: str = DEFAULT_REGION):
        region, solver_id, _ = resolve(
            "single", JUDGE_MODEL_SHORT, "opus46", "sb", 42, region
        )
        self.region = region
        self.model_id = solver_id
        cfg = bedrock_boto_config()
        self.client = boto3.client("bedrock-runtime", region_name=region, config=cfg)

    def judge(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        *,
        require_json: bool = True,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Call Sonnet; parse JSON from response.

        Returns parsed JSON dict. On failure to parse JSON, retries with a
        clarifying nudge appended to user_prompt.
        """
        attempt = 0
        last_text = ""
        last_err = None
        cur_user_prompt = user_prompt
        while attempt < max_retries:
            attempt += 1
            try:
                resp = self.client.converse(
                    modelId=self.model_id,
                    system=[{"text": system_prompt}],
                    messages=[{"role": "user", "content": [{"text": cur_user_prompt}]}],
                    inferenceConfig={
                        "temperature": temperature,
                        "maxTokens": max_tokens,
                    },
                )
                # Extract text from response
                output = resp.get("output", {})
                msg = output.get("message", {})
                content_blocks = msg.get("content", [])
                text = "".join(b.get("text", "") for b in content_blocks)
                last_text = text
                if not require_json:
                    return {"_raw": text}
                # Try to extract JSON
                obj = _extract_json(text)
                if obj is not None:
                    return obj
                # Retry with JSON nudge
                cur_user_prompt = (
                    user_prompt
                    + "\n\nReminder: your previous response did not contain valid JSON."
                    " Output ONLY valid JSON, no prose before or after."
                )
            except Exception as e:
                last_err = e
                if attempt >= max_retries:
                    raise
                time.sleep(2.0 * attempt)
        if last_err:
            raise last_err
        raise RuntimeError(f"Judge failed after {max_retries} retries. Last text: {last_text[:500]}")


def _extract_json(text: str) -> dict | None:
    """Extract the first valid JSON object from text.

    Tries: raw parse, fenced ```json block, fenced ``` block, brace-balance scan.
    """
    text = text.strip()
    # Try raw
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try fenced json block
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try first top-level {...}
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                blob = text[start : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    pass
    return None
