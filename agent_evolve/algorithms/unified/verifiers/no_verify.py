"""NoVerify — trivial verifier that always accepts without rollback."""

from __future__ import annotations

from typing import Any

from ..registry import register_verifier
from ..types import Verdict


@register_verifier("NoVerify")
class NoVerify:
    def check(
        self,
        workspace: Any,
        context: Any,
        reports: list,
        trial: Any,
        history: Any,
        state: dict[str, Any],
    ) -> Verdict:
        return Verdict(accept=True, rollback=False, reason="no-verify")
