"""Workspace structure validation."""

from __future__ import annotations

from pathlib import Path

from .manifest import CURRENT_CONTRACT_VERSION


def validate_workspace(root: str | Path) -> list[str]:
    """Validate that a directory conforms to the file system contract.

    Returns a list of error messages. Empty list means valid.
    """
    root = Path(root)
    errors: list[str] = []

    if not root.is_dir():
        return [f"Workspace root does not exist: {root}"]

    # manifest.yaml is required
    manifest_path = root / "manifest.yaml"
    if not manifest_path.exists():
        errors.append("Missing manifest.yaml")
    else:
        try:
            import yaml

            with open(manifest_path) as f:
                raw = yaml.safe_load(f) or {}
            if "name" not in raw:
                errors.append("manifest.yaml missing required field: name")
            cv = raw.get("contract_version", "")
            if cv and cv not in {"1.0", CURRENT_CONTRACT_VERSION}:
                errors.append(
                    f"Contract version mismatch: got {cv}, expected one of "
                    f"{{'1.0', {CURRENT_CONTRACT_VERSION!r}}}"
                )
        except Exception as e:
            errors.append(f"Failed to parse manifest.yaml: {e}")

    # prompts/system.md is required
    system_prompt = root / "prompts" / "system.md"
    if not system_prompt.exists():
        errors.append("Missing prompts/system.md (required)")

    # Optional directories -- warn if evolvable_layers reference them but they don't exist
    for layer_dir in ["skills", "tools", "memory"]:
        d = root / layer_dir
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)

    return errors
