"""Registry of solve-time adaptation operators (the plugin point).

``build_adapter(name, ...)`` returns the operator for a given name. The
default names ``whole_store`` / ``tree_routing`` reproduce exactly the
objects the solve loop built before this registry existed, so leaving
``--adaptation`` unset is a behavioral no-op.

Catalog/index construction and any embedding-model / Bedrock dependency
load happen ONLY inside the retrieval builders (M1/M2), so existing runs
never touch them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _build_whole_store(**_):
    from .adaptation import WholeStoreAdaptation
    return WholeStoreAdaptation()


def _build_tree_routing(*, evolver, strategy_tree, **_):
    from ..algorithms.navigation import TreeRoutingAdaptation
    return TreeRoutingAdaptation(evolver, strategy_tree)


def _build_index(workspace, out_dir, config):
    from .catalog import Catalog, CatalogIndex
    kinds = tuple(
        (config.extra.get("adaptation", {}) if config else {})
        .get("catalog_kinds", ("skill", "tool", "memory"))
    )
    catalog = Catalog.from_workspace(workspace, kinds=kinds)
    cache_dir = Path(out_dir) / ".adaptation_cache" if out_dir else None
    index = CatalogIndex(catalog, cache_dir=cache_dir)
    index.build()
    logger.info("Adaptation catalog: %d items (kinds=%s) indexed", len(catalog), kinds)
    return index


def _adapt_cfg(config, key, default):
    if not config:
        return default
    return config.extra.get("adaptation", {}).get(key, default)


def _build_retrieval(*, workspace, out_dir, config, **_):
    from .adaptation_retrieval import RetrievalAdaptation
    index = _build_index(workspace, out_dir, config)
    return RetrievalAdaptation(index, top_k=int(_adapt_cfg(config, "top_k", 8)))


def _build_agentic_filter(*, workspace, out_dir, config, **_):
    from .adaptation_retrieval import AgenticFilterAdaptation
    from ..llm.bedrock import BedrockProvider
    index = _build_index(workspace, out_dir, config)
    region = (config.extra.get("region") if config else None) or "us-west-2"
    # Use the SOLVER-class model for the lightweight selection call.
    model_id = _adapt_cfg(config, "filter_model", "us.anthropic.claude-sonnet-4-6")
    llm = BedrockProvider(model_id=model_id, region=region)
    return AgenticFilterAdaptation(
        index,
        top_k=int(_adapt_cfg(config, "candidate_k", 16)),
        keep_k=int(_adapt_cfg(config, "keep_k", 8)),
        llm=llm,
    )


_REGISTRY = {
    "whole_store": _build_whole_store,
    "tree_routing": _build_tree_routing,
    "retrieval": _build_retrieval,
    "agentic_filter": _build_agentic_filter,
}


def build_adapter(
    name: str | None,
    *,
    evolver: Any = None,
    strategy_tree: Any = None,
    navigation_enabled: bool = False,
    workspace: Any = None,
    out_dir: Any = None,
    config: Any = None,
):
    """Construct the adaptation operator for ``name``.

    ``name=None`` resolves to the legacy default: ``tree_routing`` when
    navigation is on with a tree, else ``whole_store`` — identical to the
    pre-registry behavior.
    """
    if name is None:
        name = "tree_routing" if (navigation_enabled and strategy_tree is not None) else "whole_store"
    builder = _REGISTRY.get(name)
    if builder is None:
        raise ValueError(
            f"Unknown adaptation operator {name!r}; "
            f"choices: {sorted(_REGISTRY)}"
        )
    return builder(
        evolver=evolver, strategy_tree=strategy_tree,
        navigation_enabled=navigation_enabled, workspace=workspace,
        out_dir=out_dir, config=config,
    )
