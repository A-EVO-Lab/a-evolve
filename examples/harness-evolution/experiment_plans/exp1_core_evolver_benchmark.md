# Exp1: Solver Evolvability Benchmark × Cross-Domain

> **Pivot (2026-04-21).** Exp1 is a **solver evolvability benchmark**, not an
> evolver ranking. We sweep the solver axis across 8 models, fix the evolver
> pool to the 3 working models (Opus 4.6, Sonnet 4.6, Qwen3 235B), and
> measure how much each solver improves from evolution relative to its own
> no-evolution baseline.
>
> **Reconciled (2026-04-24) to operational reality.** The benchmark set is
> 4 benchmarks (swe / mcp / tb / sb) per DEC-2 revised. The default sweep
> is single-seed (42) per DEC-7 because V3 adapters do not honour `--seed`
> for task-order reshuffle. Baseline routing uses the 4 dedicated V3
> baseline wrappers (see AC-6 in `docs/plan_v1.md`).

## Research Questions
- **RQ1a**: 不同 solver 的 evolvability 排名是什么？
- **RQ1b**: 该排名是否跨 task domain 一致？是否存在 universal most-evolvable solver？
- **RQ1c**: 对每个 solver, 最佳 evolver 是哪一个？是否 solver 决定 evolver 选择？

## Hypotheses
- Frontier closed solvers (Opus 4.6) will show **low** evolvability (ceiling effect — already strong).
- Mid-size open solvers (Qwen3 235B, Kimi K2.5) will show the **largest** evolvability (more headroom above a weaker baseline).
- The smallest solvers (Qwen3-32B, Haiku 4.5) may show **negative or flat** evolvability — they cannot execute the evolved workspace instructions even when the workspace is improved.
- Cross-domain ranking will be **moderately consistent** (Spearman ρ ≈ 0.5–0.7), with coding-domain evolvability tracking solver coding capability more strongly than MCP/SB.
- Best-evolver identity will correlate with solver family on the diagonal (same-family pair wins) **only for Claude**, because Opus/Sonnet share representation; for Qwen solvers the Qwen-235B evolver may or may not dominate.

---

## Experimental Setup

### Independent Variables

**Solver (8, all Bedrock):**

| # | Family | Model | Bedrock Model ID | Short |
|---|--------|-------|-------------------|-------|
| S1 | Claude | Opus 4.6 | `us.anthropic.claude-opus-4-6-v1` | opus46 |
| S2 | Claude | Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | sonnet46 |
| S3 | Claude | Haiku 4.5 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | haiku45 |
| S4 | OpenAI | gpt-oss-120b | `openai.gpt-oss-120b-1:0` | gptoss120b |
| S5 | Qwen | Qwen3 235B A22B 2507 | `qwen.qwen3-235b-a22b-2507-v1:0` | qwen235b |
| S6 | Qwen | Qwen3-32B | `qwen.qwen3-32b-v1:0` | qwen32b |
| S7 | MiniMax | MiniMax M2.5 | `minimax.minimax-m2.5` | minimax |
| S8 | Kimi | Kimi K2.5 | `moonshotai.kimi-k2.5` | kimi |

**Evolver (FIXED pool of 3):**

| # | Model | Bedrock Model ID | Short |
|---|-------|-------------------|-------|
| V1 | Opus 4.6 | `us.anthropic.claude-opus-4-6-v1` | opus46 |
| V2 | Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | sonnet46 |
| V3 | Qwen3 235B A22B 2507 | `qwen.qwen3-235b-a22b-2507-v1:0` | qwen235b |

> Other solver-only models (haiku45, gptoss120b, qwen32b, minimax, kimi) were evaluated as evolvers in Exp1_v2 and found to produce zero-diff cycles, workspace-contract violations, or routinely gate-rollbacked mutations. They remain in the solver pool.

**Benchmark (4):**

| ID | Benchmark | Default algorithm (V3 unified engine) | LIMIT / BATCH | Evaluation |
|----|-----------|----------------------------------------|---------------|------------|
| B1 | `swe` — SWE-bench Verified Mini | `solver_proposal` recipe         | 50 / 5        | Docker + test pass/fail |
| B2 | `mcp` — MCP-Atlas               | `adaptive_evolve` recipe         | 100 / 30      | LLM-as-judge, per-claim coverage |
| B3 | `tb`  — Terminal-Bench 2.0      | `drafts` recipe                  | 20 / 5        | Docker + `test.sh` pass/fail |
| B4 | `sb`  — SkillsBench             | `skillforge` grind (inner cycle=2)| 20 / 1       | Reward + binary pass |

> **tb caveat**: tb's V3 adapter defaults to `shuffle=True` and the runner does NOT seed `random.shuffle`, so even a single `seed=42` tb run is a draw from an unseeded shuffle distribution. tb is included for domain coverage but is **NOT co-equal** with swe/mcp/sb for paper-grade cross-benchmark ranking claims (see Analysis §6). Excluded from Spearman cross-domain consistency until a V3 patch seeds the tb shuffle deterministically (deferred follow-up).

### Controlled Variables (per DEC-6 iteration 4 / DEC-7)

| Parameter | Value |
|-----------|-------|
| `--passes` | 1 (default; `CYCLES = 1 × ⌈LIMIT / BATCH_SIZE⌉` for swe/mcp/tb) |
| Inner cycle (sb) | 2 (grind-retry per task) |
| Default seed | **42 (single seed only, per DEC-7)** |
| Solver/evolver max tokens | swe/tb 16384, mcp/sb 64000 (per `BENCHMARK_DEFAULTS`) |
| temperature | 0.0 |
| Holdout ratio | 0.2 (V3 default) |

### Baselines

**Per-solver no-evolution** (`--evolver none`) for each solver × benchmark × seed=42 = 8 × 4 × 1 = **32 baseline runs**. These are the subtrahend in every evolvability calculation.

Baseline routing uses **dedicated V3 baseline wrappers** (NO `EvolutionEngine`, NO evolver LLM, NO workspace mutation):

| Benchmark | Wrapper | Done-marker |
|-----------|---------|-------------|
| `swe`     | `examples/swe_examples/run_solve_all.sh`              | `results.json` |
| `mcp`     | `examples/mcp_examples/run_adaptive_evolve_baseline.sh` | `summary.csv` |
| `tb`      | `examples/tb_examples/run_baseline.sh`                | `results.jsonl` |
| `sb`      | `examples/skillbench_examples/run_skillbench_solve_all.sh` | `summary.txt` |

No random-mutation baseline (deprioritized — we already know only the 3 fixed evolvers produce useful mutations).

---

## Run Matrix

### Run ID Convention

```
{solver_short}_x_{evolver_short}_{benchmark}_s{seed}
```

For no-evolution baselines: `{solver_short}_x_none_{benchmark}_s{seed}`.

Examples:
- `kimi_x_opus46_mcp_s42` = Kimi solver, Opus evolver, MCP-Atlas.
- `kimi_x_none_swe_s42` = Kimi solver, baseline route on SWE.

### Evolution runs

8 solvers × 3 evolvers × 4 benchmarks × 1 seed = **96 evolution runs**.

### Baseline runs

8 solvers × 4 benchmarks × 1 seed = **32 no-evolution baseline runs**.

### Scale Summary

| Category | Runs |
|----------|------|
| Evolution (phase1 default)  | 96 |
| No-evolution baselines (phase1 opt-in via `--evolver none`) | 32 |
| **Total full matrix**        | **128** |

> Per DEC-7: single-seed default is the new scale. Previous 3-seed scale (288 evolve + 96 baseline = 384 cells) is not produced by this plan; to resurrect it V3 must first gain seed-driven task reshuffle across all 4 benchmarks.

---

## Output Directory

```
results/exp1_v3/
  {solver}_x_{evolver}_{bm}_s{seed}/
    BENCHMARK_REPORT.md         # route + done_marker + score_kind (authoritative)
    evolve.log                  # wrapper-generated stdout/stderr
    # Evolve cells:
    results.jsonl               # per-cycle scores
    results.metrics.json        # aggregate (final_score + history) — done-marker
    workspace/                  # evolved workspace
    # Baseline cells (per-benchmark):
    results.json        (swe)
    summary.csv         (mcp)
    results.jsonl       (tb)
    summary.txt         (sb)
```

---

## Dependent Variables

| Metric | Definition | Purpose |
|--------|------------|---------|
| **holdout_noevo(solver, bm, seed=42)** | Score from the baseline wrapper's done-marker, solver = `solver`. | Subtrahend. |
| **holdout_withevo(solver, evolver, bm, seed=42)** | `results.metrics.json.final_score` after evolve run. | Minuend. |
| **Lift(solver, evolver, bm)** | `holdout_withevo − holdout_noevo` | Signed per-evolver lift (single-seed point estimate). |
| **Evolvability(solver, bm)** | `max_evolver Lift(solver, evolver, bm)` | **Core metric**. |
| **Best evolver** | `argmax_evolver Lift` | Pairing map. |
| **Cross-domain consistency** | Spearman ρ of Evolvability across `{swe, mcp, sb}` (tb excluded per tb caveat) | Universality test. |
| Convergence speed | Cycles until `score_history` reaches 90% of final | Efficiency. |
| Cost | $ per cell from Bedrock token counts (where available) | For Exp6 reuse. |
| Mutation / rollback rate | From wrapper log + `results.jsonl` | Quality, for Exp7. |

---

## Analysis Plan

> **Statistical protocol note (per DEC-7).** The previous 3-seed, repeated-measures protocol (Friedman, Wilcoxon) assumed seed reproducibility across `{42, 123, 456}`. Under single-seed reality, Exp1 provides **point estimates** rather than variance-estimable measurements. Headline claims in the paper must be qualified as "single-seed lower-bound on evolvability"; statistical tests that need repeated measures (Friedman, Wilcoxon signed-rank) are **deferred until V3 adds seed-driven task reshuffle**. Bootstrap over tasks (within a single seed) is acceptable for uncertainty intervals on the mean score inside a cell.

### 1. Headline heatmap
- 8 × 3 matrix per benchmark: rows = solver, columns = evolver, cells = Lift at seed=42.
- Add an 8 × 1 side column: Evolvability (max Lift across the 3 evolvers).

### 2. Cross-domain consistency (excluding tb)
- Compute Spearman rank correlation of Evolvability between each pair of `{swe, mcp, sb}`.
- Report ρ(swe, mcp), ρ(swe, sb), ρ(mcp, sb).
- ρ > 0.7 → "universal evolvability ranking exists."
- ρ < 0.5 → "evolvability is domain-specific."
- tb ranking reported separately as a non-primary domain (see §6).

### 3. Statistical testing (limited under single-seed)
- **Descriptive**: mean Lift per cell at seed=42, bootstrap-CI over tasks within the cell.
- **Deferred**: Friedman / Wilcoxon across seeds — require V3 shuffle-seed patch first.

### 4. Same-family vs cross-family (feeds Exp2)
- Claude-family pairings: {opus46, sonnet46, haiku45} solvers × {opus46, sonnet46} evolvers.
- Qwen-family pairings: {qwen235b, qwen32b} solvers × {qwen235b} evolver.
- Diagonal (same-family) vs off-diagonal (cross-family) Lift means.

### 5. Evolvability vs solver strength
- Plot Evolvability (y) vs holdout_noevo (x) per solver.
- Expected: negative correlation (ceiling effect) OR inverted-U (mid-strength solvers gain most).

### 6. Best-evolver map
- 8-row table: solver → winning evolver per benchmark. Confidence = bootstrap over tasks at seed=42.

### 7. tb-domain treatment
- Report tb Evolvability for completeness, but call it out as "non-reproducible sample" in every table caption.
- Exclude tb from universal-ranking claims until V3 shuffle-seed lands.

---

## Execution

Launch via `phase1_single_seed.sh` (single-seed default; 96 evolve cells):

```bash
bash scripts/phase1_single_seed.sh                   # full 96-cell evolve sweep (single region)
bash scripts/phase1_single_seed.sh --evolver none    # 32-cell baseline sweep (opt-in)
bash scripts/check_status.sh                         # route-aware solver × evolver matrix
```

### Multi-region routing

For full-matrix sweeps where Bedrock per-region quotas can throttle a
single-region run, use `--region-strategy hash` to spread cells across
the verified `(model, region)` pairs in `model_region_availability.json`:

```bash
# Hash-route the 96 evolve cells across 5–7 regions per pair
REGION_STRATEGY=hash bash scripts/phase1_single_seed.sh

# Or the same with explicit flag
bash scripts/phase1_single_seed.sh --region-strategy hash
```

The hash strategy is deterministic — re-launching the same cell always
maps to the same region — so phase1's skip-logic still works. **Cells
that already exist on disk under a different `(strategy, region)`** will
emit a non-interactive `[CONFLICT]` line and the launcher will exit
non-zero for that cell. Two ways to recover:

1. Continue the previous sweep:
   `bash scripts/phase1_single_seed.sh --region-strategy <prior-strategy>`
2. Force a clean relaunch (renames mismatched cells to
   `<cell>.legacy.<ts>` and re-launches in fresh dirs):
   `bash scripts/phase1_single_seed.sh --region-strategy hash --force-relaunch-legacy`

See `CLAUDE.md` Multi-region routing section for the full sidecar
field reference.

Skip logic and score reading are **sidecar-driven**: each cell's `BENCHMARK_REPORT.md` (authored by `run_exp1.py`) names the `done_marker:` and `score_kind:`. `scripts/phase1_single_seed.sh` and `scripts/check_status.sh` both read the sidecar via `scripts/lib/read_sidecar.sh`'s `cell_score_with_fallback()`, which infers the contract from `(evolver, benchmark)` when the sidecar is absent (legacy cells predating the sidecar + first launches). Evolve cells: `results.metrics.json` → `final_score`. Baseline cells: swe `results.json` (JSON list; fraction with `success=true`), mcp `summary.csv` from `adaptive_evolve_baseline.py` (mean of the `score` column, matching evolve-route fractional `final_score`), tb `results.jsonl` (fraction with `passed=true`), sb `summary.txt` (`pass / tasks_total`). Re-launching is safe.

### Preflight and infra

- `phase0_smoke.sh` runs a wrapper-patch preflight (verifies `EVOLVER_MODEL_ID` in swe/tb/sb evolve wrappers; `--max-cycles` in sb; SWE/TB agent-constructor propagation; cursor-patched benchmark adapters; baseline wrappers exist).
- Docker data-root on local NVMe (`/var/lib/docker`); don't switch to Lustre.
- FSX image archive at `/fsx-shared/juncheng/docker-image-cache/*.tar.zst` is the second-tier cache for evicted images.

### Timing estimate (single-seed)

| Per-cell wall clock | Cells | Concurrency | Wall time |
|---------------------|-------|-------------|-----------|
| ~2–4 h (swe), ~2 h (mcp), ~1 h (tb), ~1 h (sb) | 96 evolve + 32 baseline = 128 | 12 concurrent | ~1.5–2.5 days |
