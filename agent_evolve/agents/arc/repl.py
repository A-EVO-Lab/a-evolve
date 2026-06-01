"""Persistent Python REPL for ARC-AGI-3 sub-agents.

Adapted from Symbolica's Agentica SDK persistent REPL concept.
Each sub-agent gets its own REPL that:
- Persists state (variables, imports) across calls
- Pre-loads numpy, Frame class, color helpers
- Exposes the current game frame as `frame` and history as `frames`
- Allows the LLM to write analysis code before choosing actions
- Captures stdout/stderr and returns it to the LLM

This is the critical missing piece: Symbolica's 36% vs raw LLM's 0.2%
came from letting agents write code to analyze grids programmatically.
"""

from __future__ import annotations

import io
import logging
import sys
import traceback
from typing import Any

logger = logging.getLogger(__name__)

# Default imports injected into every REPL
_PRELUDE = """\
import numpy as np
from collections import Counter, defaultdict
"""


class PersistentREPL:
    """A persistent Python execution environment for grid analysis.

    State persists across exec() calls -- variables defined in one call
    are available in the next. Pre-loaded with numpy, Frame helpers, and
    the current game state.

    Usage::

        repl = PersistentREPL()
        repl.update_frame(frame, frames, meta)

        result = repl.exec('''
            # Find all red pixels
            red = frame.find(8)
            print(f"Found {len(red)} red pixels")
            bbox = frame.bounding_box(8)
            print(f"Red bounding box: {bbox}")
        ''')
        print(result.output)  # "Found 12 red pixels\nRed bounding box: (10, 20, 15, 25)"

        # State persists
        result = repl.exec('print(f"Still have {len(red)} reds")')
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._globals: dict[str, Any] = {"__builtins__": __builtins__}
        self._exec_count = 0

        # Run prelude
        exec(_PRELUDE, self._globals)

        # Inject Frame and color helpers
        try:
            from .frame import Frame, DiffRegion
            from .colors import COLOR_NAMES, PALETTE_HEX, COLOR_LEGEND

            self._globals.update({
                "Frame": Frame,
                "DiffRegion": DiffRegion,
                "COLOR_NAMES": COLOR_NAMES,
                "PALETTE_HEX": PALETTE_HEX,
                "COLOR_LEGEND": COLOR_LEGEND,
            })
        except ImportError:
            pass

    def update_frame(
        self,
        frame: Any,
        frames: list | None = None,
        meta: dict | None = None,
    ) -> None:
        """Update the REPL's game state variables.

        After this call, the REPL has:
        - ``frame``: the current Frame object
        - ``grid``: the raw grid as numpy array (frame.grid_np)
        - ``frames``: list of all frames so far
        - ``prev_frame``: the previous frame (or None)
        - ``meta``: game metadata dict
        - ``level``: current level number
        - ``available_actions``: list of action name strings
        """
        self._globals["frame"] = frame
        self._globals["frames"] = frames or []
        self._globals["prev_frame"] = frames[-2] if frames and len(frames) >= 2 else None
        self._globals["meta"] = meta or {}
        self._globals["level"] = (meta or {}).get("levels_completed", 0)
        self._globals["available_actions"] = (meta or {}).get("available_actions", [])

        # Expose grid as numpy for direct manipulation
        if hasattr(frame, "grid_np"):
            self._globals["grid"] = frame.grid_np
        elif hasattr(frame, "grid"):
            import numpy as np
            self._globals["grid"] = np.array(frame.grid, dtype=np.int8)

    def exec(self, code: str, max_output: int = 3000) -> REPLResult:
        """Execute Python code in the persistent REPL.

        Args:
            code: Python code to execute.
            max_output: Max characters of captured stdout/stderr.

        Returns:
            REPLResult with output, error, and success flag.
        """
        self._exec_count += 1

        # Capture stdout/stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        captured_out = io.StringIO()
        captured_err = io.StringIO()

        try:
            sys.stdout = captured_out
            sys.stderr = captured_err

            # Execute in persistent namespace
            exec(code, self._globals)

            stdout = captured_out.getvalue()
            stderr = captured_err.getvalue()

            output = stdout
            if stderr:
                output += f"\n[stderr]: {stderr}"

            if len(output) > max_output:
                output = output[:max_output] + f"\n... [truncated, {len(output)} total chars]"

            return REPLResult(
                output=output or "(no output -- use print() to see results)",
                error=None,
                success=True,
            )

        except Exception as e:
            tb = traceback.format_exc()
            # Keep it concise
            error_msg = f"{type(e).__name__}: {e}"
            if len(tb) > 1000:
                tb = tb[-1000:]

            return REPLResult(
                output=captured_out.getvalue()[:max_output],
                error=f"{error_msg}\n{tb}",
                success=False,
            )

        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def get_variable(self, name: str) -> Any:
        """Get a variable from the REPL namespace."""
        return self._globals.get(name)

    def set_variable(self, name: str, value: Any) -> None:
        """Set a variable in the REPL namespace."""
        self._globals[name] = value

    @property
    def exec_count(self) -> int:
        return self._exec_count

    def __repr__(self) -> str:
        user_vars = [
            k for k in self._globals
            if not k.startswith("_") and k not in (
                "np", "Counter", "defaultdict", "Frame", "DiffRegion",
                "COLOR_NAMES", "PALETTE_HEX", "COLOR_LEGEND",
            )
        ]
        return f"REPL(execs={self._exec_count}, vars={user_vars})"


class REPLResult:
    """Result of a REPL execution."""

    __slots__ = ("output", "error", "success")

    def __init__(self, output: str, error: str | None, success: bool):
        self.output = output
        self.error = error
        self.success = success

    def __str__(self) -> str:
        if self.success:
            return self.output
        return f"ERROR: {self.error}\nOutput before error: {self.output}"

    def __repr__(self) -> str:
        status = "OK" if self.success else "ERROR"
        return f"REPLResult({status}, {len(self.output)} chars)"
