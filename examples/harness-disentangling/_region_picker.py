"""Deterministic, fail-fast region resolver for EvolverBench cells.

Single source of truth for `(model_short, region) → region-aware Bedrock
model_id` mapping plus per-cell region selection. Reads
`model_region_availability.json` (the verified live availability matrix);
never silently falls back when the JSON is missing, malformed, or lacks
the requested entry.

Two strategies:
  - "single": preserve current behaviour. The caller's explicit region is
    honoured; the JSON is consulted only to resolve the region-specific
    `model_id` (Anthropic prefix `us.` ↔ `eu.` is data-driven from the
    JSON's per-region `model_id` field — no string surgery).
  - "hash": deterministic per-cell pick from the verified intersection
    of solver-OK ∩ evolver-OK regions (or solver-only for `--evolver none`
    baseline route). Uses hashlib.sha1 over a canonical key so that two
    separate Python processes — possibly with different `PYTHONHASHSEED`
    — pick the same region for the same cell. Python's built-in `hash()`
    is forbidden because it is process-randomized.

Public surface:
  resolve(strategy, solver_short, evolver_short, benchmark, seed, explicit_region)
      → (region, solver_model_id, evolver_model_id_or_empty)

  load_region_db(path=None) → dict
      Lazy loader; raises with a clear message on missing/malformed input.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_REGION_DB_PATH = REPO_ROOT / "model_region_availability.json"

# Short-name → JSON's long-name key. Keep in sync with run_exp1.py's
# SOLVER_MODELS / EVOLVER_MODELS short-names.
SOLVER_LONG: dict[str, str] = {
    "opus46":     "Claude Opus 4.6",
    "sonnet46":   "Claude Sonnet 4.6",
    "haiku45":    "Claude Haiku 4.5",
    "gptoss120b": "gpt-oss-120b",
    "qwen235b":   "Qwen3-235B",
    "qwen32b":    "Qwen3-32B",
    "minimax":    "MiniMax M2.5",
    "kimi":       "Kimi K2.5",
}
# Default evolver pool is the 3 working evolvers (per plan_v1 design — what
# Exp1 sweeps over). Exp0 / RQ1 needs a larger builder-side pool; callers
# pass their own expanded mapping via `resolve(..., evolver_lookup=...)`.
# Do NOT mutate this default — Exp1 cells already on disk depend on it.
EVOLVER_LONG: dict[str, str] = {
    k: SOLVER_LONG[k] for k in ("opus46", "sonnet46", "qwen235b")
}

# Regionless/local evolver ids. These are not Bedrock model ids and should
# not be looked up in model_region_availability.json; the solver still picks
# a Bedrock region as usual, while the evolver is routed by the OpenAI-compatible provider.
LOCAL_EVOLVER_IDS: dict[str, str] = {
    "qwen35_9b": "/fsx/models/Qwen3.5-9B",
}

# Exp0 / RQ1 selected builder-side pool. Values are either region DB long-name
# keys (for Bedrock models) or literal regionless provider ids / local paths.
EVOLVER_LONG_EXP0: dict[str, str] = {
    k: SOLVER_LONG[k] for k in ("opus46", "sonnet46", "haiku45", "qwen235b", "qwen32b", "gptoss120b")
}
EVOLVER_LONG_EXP0.update(LOCAL_EVOLVER_IDS)

# Convenience: full Bedrock builder-side pool plus local ids for ad-hoc sweeps.
EVOLVER_LONG_FULL: dict[str, str] = dict(SOLVER_LONG)
EVOLVER_LONG_FULL.update(LOCAL_EVOLVER_IDS)


class RegionResolveError(ValueError):
    """Raised on any unrecoverable routing/resolution failure."""


def load_region_db(path: str | Path | None = None) -> dict:
    """Load and validate `model_region_availability.json`. Fail-fast."""
    p = Path(path) if path else DEFAULT_REGION_DB_PATH
    if not p.is_file():
        raise RegionResolveError(f"Region DB not found: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise RegionResolveError(f"Region DB malformed JSON ({p}): {e}") from e
    if not isinstance(raw, dict):
        raise RegionResolveError(
            f"Region DB top-level must be a dict, got {type(raw).__name__}: {p}"
        )
    return raw


def _ok_entries_for(
    model_short: str, db: dict, lookup: dict[str, str], role: str = "model"
) -> dict[str, str]:
    """Return {region: model_id} for OK entries of a model. Fail-fast.

    `role` (one of "solver", "evolver", "model") customises the error text
    so CLI / library callers see the role-specific contract from
    plan_v2 AC-7 ("Unknown solver short-name: ...", "Unknown evolver
    short-name: ...").
    """
    long_name = lookup.get(model_short)
    if long_name is None:
        # Plan AC-7 contract: `Unknown <role> short-name: <value>`.
        # The "; expected one of [...]" suffix is informational; the
        # tests + CLI check assert the literal prefix.
        raise RegionResolveError(
            f"Unknown {role} short-name: {model_short}; expected one of "
            f"{sorted(lookup)}"
        )
    if long_name not in db:
        raise RegionResolveError(
            f"{role.capitalize()} {long_name!r} (short {model_short!r}) "
            f"missing from region DB"
        )
    entries = db[long_name]
    if not isinstance(entries, list):
        raise RegionResolveError(
            f"Region DB entry for {long_name!r} must be a list, got "
            f"{type(entries).__name__}"
        )
    ok: dict[str, str] = {}
    for e in entries:
        if not isinstance(e, dict):
            raise RegionResolveError(
                f"Region DB entry for {long_name!r} has non-dict member: {e!r}"
            )
        if e.get("status") != "OK":
            continue
        region = e.get("region")
        model_id = e.get("model_id")
        if not region or not model_id:
            raise RegionResolveError(
                f"Region DB OK entry for {long_name!r} missing region/model_id: {e!r}"
            )
        ok[region] = model_id
    if not ok:
        raise RegionResolveError(
            f"Model {long_name!r} has no OK regions in region DB"
        )
    return ok


def _is_regionless_evolver_ref(model_ref: str) -> bool:
    """Whether an evolver id is independent of the Bedrock region DB."""
    return (
        model_ref.startswith("/")
        or model_ref.startswith("file:")
        or model_ref.startswith("openai:")
    )


def _stable_pick(items: list[str], key: str) -> str:
    """Deterministic across processes: sha1(key) mod len(items).

    Python's built-in `hash()` is process-randomized via PYTHONHASHSEED;
    using it here would break re-launch idempotency. SHA-1 is just a
    fast, well-distributed hash — not a security choice.
    """
    if not items:
        raise RegionResolveError(f"No candidate regions for key={key!r}")
    digest = hashlib.sha1(key.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:8], "big") % len(items)
    return items[idx]


def resolve(
    strategy: str,
    solver_short: str,
    evolver_short: str,
    benchmark: str,
    seed: int,
    explicit_region: str | None,
    db: dict | None = None,
    *,
    evolver_lookup: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Resolve a cell's `(region, solver_model_id, evolver_model_id)`.

    `evolver_model_id` is the literal empty string for `--evolver none`
    (baseline route). The solver's id is never aliased into the evolver
    slot.

    `evolver_lookup` overrides the default (3-evolver) `EVOLVER_LONG`
    table. Exp1 callers leave it unset to preserve bit-for-bit behaviour;
    Exp0 / RQ1 callers pass a larger builder-side pool (e.g.
    `EVOLVER_LONG_EXP0`). Lookup values may be region DB long-name keys or
    regionless provider ids / local paths. The solver lookup is unchanged.
    Hash-strategy region pick is keyed only on short-names + benchmark +
    seed + route, so existing Exp1 cells re-resolve to the same region
    regardless of which lookup table is used here.

    Raises `RegionResolveError` on:
      - unknown strategy
      - unknown solver/evolver short-name
      - missing model entry in the region DB
      - explicit region not OK for the requested model
      - hash strategy with empty intersection (should not occur for the
        verified Exp1 matrix, but defended for future regressions)
    """
    if strategy not in ("single", "hash"):
        raise RegionResolveError(
            f"Unknown region strategy {strategy!r}; expected 'single' or 'hash'"
        )

    db = db if db is not None else load_region_db()
    is_baseline = (evolver_short == "none")
    e_lookup = evolver_lookup if evolver_lookup is not None else EVOLVER_LONG
    s_regions = _ok_entries_for(solver_short, db, SOLVER_LONG, role="solver")
    evolver_ref: str | None = None
    evolver_regionless = False
    if is_baseline:
        e_regions: dict[str, str] = {}
    else:
        evolver_ref = e_lookup.get(evolver_short)
        if evolver_ref is None:
            raise RegionResolveError(
                f"Unknown evolver short-name: {evolver_short}; expected one of "
                f"{sorted(e_lookup)}"
            )
        evolver_regionless = _is_regionless_evolver_ref(evolver_ref)
        e_regions = (
            {} if evolver_regionless
            else _ok_entries_for(evolver_short, db, e_lookup, role="evolver")
        )

    if strategy == "single":
        if explicit_region is None:
            raise RegionResolveError(
                "single strategy requires explicit_region (no auto-pick)"
            )
        region = explicit_region
        # Plan AC-7 contract: `<short> unavailable in <region> (status: FAIL)`.
        # No role prefix; literal "FAIL".
        if region not in s_regions:
            raise RegionResolveError(
                f"{solver_short} unavailable in {region} (status: FAIL)"
            )
        if not is_baseline and not evolver_regionless and region not in e_regions:
            raise RegionResolveError(
                f"{evolver_short} unavailable in {region} (status: FAIL)"
            )
        evolver_id = (
            "" if is_baseline
            else evolver_ref if evolver_regionless
            else e_regions[region]
        )
        return (region, s_regions[region], evolver_id)

    # strategy == "hash"
    if is_baseline or evolver_regionless:
        candidates = sorted(s_regions.keys())
    else:
        candidates = sorted(set(s_regions.keys()) & set(e_regions.keys()))
    route = "baseline" if is_baseline else "evolve"
    key = f"{solver_short}|{evolver_short}|{benchmark}|{seed}|{route}"
    region = _stable_pick(candidates, key)
    evolver_id = (
        "" if is_baseline
        else evolver_ref if evolver_regionless
        else e_regions[region]
    )
    return (region, s_regions[region], evolver_id)


__all__ = [
    "RegionResolveError",
    "SOLVER_LONG",
    "EVOLVER_LONG",
    "LOCAL_EVOLVER_IDS",
    "EVOLVER_LONG_EXP0",
    "EVOLVER_LONG_FULL",
    "load_region_db",
    "resolve",
]
