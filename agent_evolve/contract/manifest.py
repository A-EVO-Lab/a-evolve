"""Manifest parsing for agent workspaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


CURRENT_CONTRACT_VERSION = "1.0"


@dataclass
class Manifest:
    """Parsed manifest.yaml for an agent workspace."""

    name: str
    version: str = "0.1.0"
    contract_version: str = CURRENT_CONTRACT_VERSION

    # Agent entrypoint: Python dotted path to a BaseAgent subclass
    entrypoint: str | None = None
    agent_type: str = "reference"  # "reference" | "custom"

    evolvable_layers: list[str] = field(default_factory=lambda: ["prompts", "skills", "memory"])
    reload_strategy: str = "hot"  # "hot" | "cold"

    @classmethod
    def from_yaml(cls, path: str | Path) -> Manifest:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        agent_block = raw.get("agent", {})
        return cls(
            name=raw.get("name", "unnamed-agent"),
            version=raw.get("version", "0.1.0"),
            contract_version=raw.get("contract_version", CURRENT_CONTRACT_VERSION),
            entrypoint=agent_block.get("entrypoint"),
            agent_type=agent_block.get("type", "reference"),
            evolvable_layers=raw.get("evolvable_layers", ["prompts", "skills", "memory"]),
            reload_strategy=raw.get("reload_strategy", "hot"),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "contract_version": self.contract_version,
            "agent": {
                "type": self.agent_type,
                "entrypoint": self.entrypoint,
            },
            "evolvable_layers": self.evolvable_layers,
            "reload_strategy": self.reload_strategy,
        }

    def save(self, path: str | Path) -> None:
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
