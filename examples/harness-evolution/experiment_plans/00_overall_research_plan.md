# EvolverBench: Systematically Benchmarking Solver **Evolvability**

## Overall Research Experiment Plan

> **Pivot (2026-04-21).** Early Exp1 runs across 9 evolver candidates showed that
> *evolver choice is not the performance bottleneck* — only a small subset of
> frontier models (Opus 4.6, Sonnet 4.6, Qwen3 235B A22B 2507) actually mutate
> workspaces in a useful way. The remaining candidates either produce no-ops,
> rollback-prone edits, or silently fail the evolver tool loop. Evolver
> ranking alone is therefore uninteresting as a research question.
>
> **The new central question is: which solvers can be evolved?** We fix the
> evolver pool to the 3 working models and sweep the solver dimension across
> every model we have access to. The dependent variable becomes a solver's
> **evolvability**: the holdout lift it gets from evolution, relative to its
> own no-evolution baseline.

---

## 1. Motivation

A-Evolve decouples the self-evolving agent framework into four axes: **Solver**, **Evolver**, **Evolution Algorithm**, **Task**. Our first wave of experiments (Exp1 v2) showed that *evolver ranking is largely flat* once we control for whether the evolver actually emits valid workspace mutations. Three models (Opus 4.6, Sonnet 4.6, Qwen3 235B) consistently produce non-trivial, gate-accepted mutations; the other six do not. Within the working three, ranking differences are small and noise-dominated.

The interesting signal is on the **other axis**: given a working evolver, *some solvers improve a lot from evolution and others do not at all*. A mid-size open solver may gain +20pp while a frontier closed solver flatlines, or vice versa. That asymmetry — call it **evolvability** — is what EvolverBench now aims to characterize.

Research questions we answer:

- Which LLMs are evolvable, and by how much?
- Does evolvability track solver size? Solver family? Closed vs open?
- Does the best evolver for a given solver depend on solver identity (solver-evolver pairing)?
- Does evolvability depend on task domain and on the evolution algorithm?
- Can evolvability be predicted from public benchmark scores?
- How does a solver's evolution trajectory differ across solvers (dynamics)?

---

## 2. Research Questions

| RQ | 问题 | 类型 |
|----|------|------|
| **RQ1** | 不同 solver 的 evolvability 排名是什么？排名是否跨 task domain 一致？ | 核心 benchmark |
| **RQ2** | Solver × Evolver 交互: (a) 同 family 优势；(b) solver 大小与最优 evolver 的关系；(c) 开源 vs 闭源 solver 的 evolvability 差异。 | 交互效应 |
| **RQ3** | 同 family 内，solver 模型规模对 evolvability 的 scaling law 是什么？ | Scaling |
| **RQ4** | 好 evolver 的组合 (Opus/Sonnet/Qwen235B) 在不同 algorithm 下是否保持有效？Algorithm 的选择会放大/缩小 solver 的 evolvability 差异吗？ | 算法特异性 |
| **RQ5** | Solver evolvability 与哪些通用能力 (public benchmarks) 相关？能否预测？ | 归因 |
| **RQ6** | 固定预算下，针对特定 solver 的最优 evolver+cycle 策略是什么？ | 实用指导 |
| **RQ7** | 不同 solver 在 evolution 过程中的轨迹差异 (收敛、稳定性、exploration/exploitation)？ | 动态 |

---

## 3. Experimental Framework

### 3.1 基础设施: A-Evolve Pipeline

Unchanged. Evolution loop per cycle: SOLVE → OBSERVE → GIT SNAPSHOT → EVOLVE → RELOAD. All models via AWS Bedrock, driven by `run_exp1.py`.

### 3.2 Model Candidates

#### Evolver pool (FIXED, 3 models)

Only models empirically shown to produce useful, gate-accepted mutations:

| # | Family | Model | Bedrock Model ID | Short Name |
|---|--------|-------|-------------------|------------|
| V1 | Claude | Opus 4.6 | `us.anthropic.claude-opus-4-6-v1` | opus46 |
| V2 | Claude | Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | sonnet46 |
| V3 | Qwen | Qwen3 235B A22B 2507 | `qwen.qwen3-235b-a22b-2507-v1:0` | qwen235b |

> Why only 3. Kimi K2.5, MiniMax M2.5, gpt-oss-120b, Qwen3-32B, Haiku 4.5 were evaluated as evolvers in Exp1_v2 and found to either (a) produce zero-diff cycles, (b) break the workspace contract, or (c) be routinely gate-rollbacked. They remain in the solver pool below.

#### Solver pool (8 models, all Bedrock)

| # | Family | Model | Bedrock Model ID | Short Name | Also an evolver? |
|---|--------|-------|-------------------|------------|------------------|
| S1 | Claude | Opus 4.6 | `us.anthropic.claude-opus-4-6-v1` | opus46 | ✅ |
| S2 | Claude | Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | sonnet46 | ✅ |
| S3 | Claude | Haiku 4.5 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | haiku45 | ❌ |
| S4 | OpenAI | gpt-oss-120b | `openai.gpt-oss-120b-1:0` | gptoss120b | ❌ |
| S5 | Qwen | Qwen3 235B A22B 2507 | `qwen.qwen3-235b-a22b-2507-v1:0` | qwen235b | ✅ |
| S6 | Qwen | Qwen3-32B | `qwen.qwen3-32b-v1:0` | qwen32b | ❌ |
| S7 | MiniMax | MiniMax M2.5 | `minimax.minimax-m2.5` | minimax | ❌ |
| S8 | Kimi | Kimi K2.5 | `moonshotai.kimi-k2.5` | kimi | ❌ |

#### Scaling chains for RQ3 (now on the SOLVER axis)

| Family | Small | Mid | Large |
|--------|-------|-----|-------|
| **Claude** | Haiku 4.5 | Sonnet 4.6 | Opus 4.6 |
| **OpenAI** | — | — | gpt-oss-120b |
| **Qwen** | Qwen3-32B | — | Qwen3 235B A22B 2507 |

### 3.3 Evolution Algorithms

| Algorithm | Class | 核心机制 | 默认 domain |
|-----------|-------|----------|-------------|
| AEvolveEngine | `algorithms/skillforge/` | LLM + bash 通用突变 | SkillsBench (sb) |
| AdaptiveEvolveEngine | `algorithms/adaptive_evolve/` | Per-claim feedback | MCP-Atlas (mcp) |
| GuidedSynthesisEngine | `algorithms/guided_synth/` | Memory-first + skill curation | SWE-bench (swe) |

Exp1 uses the per-benchmark best algorithm (the `BEST_ALGO_PER_BENCHMARK` mapping in `run_exp1.py`); Exp4 varies algorithm.

### 3.4 Benchmarks (4 active)

| Benchmark | Domain | Evaluation | Notes |
|-----------|--------|------------|-------|
| SWE-bench Verified Mini (`swe`) | 代码修复 | Docker + test pass/fail | LIMIT=50, BATCH=5 |
| MCP-Atlas (`mcp`) | 工具调用 | LLM-as-judge, per-claim coverage | LIMIT=100, BATCH=30 |
| Terminal-Bench 2.0 (`tb`) | Sysadmin/coding challenges | Docker + `test.sh` pass/fail | LIMIT=20, BATCH=5. **Caveat**: adapter `shuffle=True` is unseeded; see §4.3 |
| SkillsBench (`sb`) | 技能发现执行 | Reward + binary pass | LIMIT=20, BATCH=1, inner-cycle=2 grind retries |

> Pivot update (2026-04-24): `tb` is re-included for domain coverage after DEC-2 was revised to 4 benchmarks. `tb` is **NOT co-equal** with swe/mcp/sb for paper-grade cross-benchmark ranking claims because its V3 adapter defaults to `shuffle=True` without the runner seeding the shuffle; a single `seed=42` tb run is a draw from the unseeded shuffle distribution. Paper-grade cross-benchmark claims must either exclude tb or defer until V3 patches the shuffle seed.

---

## 4. Experiment Overview

### 4.1 实验矩阵 (revised per DEC-2 rev / DEC-7)

| 实验 | RQ | Sweep | Fixed | 规模 | 优先级 |
|------|----|-------|-------|------|--------|
| **Exp1** | RQ1 | 8 solvers × 3 evolvers × 4 benchmarks × 1 seed (42) + 8 × 4 no-evo baselines | algo=best-per-bm | **96 evolve cells + 32 baseline cells = 128 cells** | **P0** |
| **Exp2** | RQ2 | Same-family / cross-family subsets of Exp1 | — | **0 new runs** (analytic) | **P0** |
| **Exp3** | RQ3 | 3 scaling chains on solver axis × 3 evolvers × 4 bm × 1 seed | — | **~0 new**, reuses Exp1 cells | **P0** |
| **Exp4** | RQ4 | 3 algos × {opus46,sonnet46,qwen235b} × subset of solvers × 2 bm | — | ~90 new runs | **P1** |
| **Exp5** | RQ5 | Analytic regression of Exp1 evolvability on public benchmarks | — | 0 runs | **P1** |
| **Exp6** | RQ6 | Per-solver Pareto frontier over evolver × passes | — | ~60 new runs | **P1** |
| **Exp7** | RQ7 | Dynamics extraction from Exp1–4 histories | — | 0 runs | **P1** |

### 4.2 数据复用

```
Exp1 (8 solvers × 3 evolvers × 4 benchmarks × 1 seed + 8 × 4 no-evo baselines)
 ├── Exp2 完全复用 (same/cross family analysis on Exp1 cells)
 ├── Exp3 完全复用 (scaling chains are subsets of solver pool)
 ├── Exp5 完全复用 (regression input)
 ├── Exp6 部分复用 (Exp1 covers --passes=1; higher-pass configs are new)
 ├── Exp7 完全复用 (dynamics from per-run score_history)
 └── Exp4 部分复用 (best-algo runs from Exp1 + new algo variants)
```

### 4.3 公共实验参数 (per DEC-6 iteration 4 / DEC-7)

| 参数 | 值 | 理由 |
|------|-----|------|
| `--passes` | 1 (default) | 一次完整 dataset sweep 足够。Higher passes (K≥2) reserved for Exp6 cost-frontier. |
| Default seed | **42 (single seed only)** | DEC-7: V3 adapters do not honour `--seed` for task-order reshuffle (swe/mcp/sb are `shuffle=False`; tb has `shuffle=True` but the runner does NOT seed `random.shuffle`). Multi-seed would 2× compute without seed-reproducibility. |
| Batch size | swe=5, mcp=30, tb=5, sb=1 | `BENCHMARK_DEFAULTS` in `run_exp1.py`. |
| Limit (dataset size) | swe=50, mcp=100, tb=20, sb=20 | |
| Inner cycle (sb) | 2 | Grind-retry per task. |
| evolver_max_tokens | 16384 | |
| solver_max_tokens | 16384 (swe/tb), 64000 (mcp/sb) | Per `BENCHMARK_DEFAULTS` MAX_TOKENS. |
| temperature | 0.0 | |
| Holdout ratio | 0.2 (V3 default) | |

### 4.3.1 Seed semantics caveat (per `CLAUDE.md` Gotchas)

| Benchmark | Task order | `--seed` effect |
|-----------|------------|------------------|
| `swe`     | FIXED (`shuffle=False`)                                            | Run-namespace + API stochasticity only |
| `mcp`     | FIXED (`shuffle=False`)                                            | Run-namespace + API stochasticity only |
| `sb`      | FIXED (upstream shuffle call is commented out)                     | Run-namespace + API stochasticity only |
| `tb`      | NONDETERMINISTIC (`shuffle=True`, NOT seeded by runner)            | Run-namespace only; task order is random but not seed-addressable |

Because no benchmark's `--seed` actually controls task order under current V3, a multi-seed sweep is not informative for variance estimation of task-order effects. Multi-seed is deferred until V3 adds deterministic seed-driven task reshuffle. If V3 later adds that patch, multi-seed protocol (3+ seeds, Friedman / Wilcoxon, variance estimation) should be restored here.

### 4.4 Baselines

- **Per-solver no-evolution** (`--evolver none`) for every solver × benchmark × seed=42. This is the subtrahend for every evolvability computation.
- Baseline route uses **dedicated V3 baseline entry points** (no EvolutionEngine): `run_solve_all.sh` (swe), `run_adaptive_evolve_baseline.sh` (mcp), `run_baseline.sh` (tb), `run_skillbench_solve_all.sh` (sb). See `scripts/README.md` for the complete baseline mapping.
- Baseline runs are **opt-in** in the phase-1 sweep (`phase1_single_seed.sh --evolver none`) because each baseline is an expensive full-dataset pass per solver.
- Random-mutation baseline **deprioritized**: the pivot is "evolver produces useful mutations"; we don't need a pure-random control to defend that any more. Kept as optional follow-up for Exp7 if needed to sanity-check edit-size effects.

### 4.5 Primary metrics

| 类别 | Metric | 公式 |
|------|--------|------|
| **Evolvability (core)** | `Evolvability(solver, bm)` | `max_v (holdout_with_evo(solver, v, bm)) − holdout_noevo(solver, bm)` |
| **Evolver-specific lift** | `Lift(solver, evolver, bm)` | `holdout_with_evo − holdout_noevo` (signed, can be negative) |
| **Best-evolver identity** | `argmax_v Lift(solver, v, bm)` | Discrete choice for pairing analysis |
| **Cross-domain consistency** | Spearman ρ of Evolvability across `{swe, mcp, sb}` (tb excluded per §3.4 caveat) | Within-solver rank stability |
| Efficiency | Convergence speed (cycles to 90% of final) | per-run |
| Cost | Total $ (solver tokens + evolver tokens × Bedrock price) | per-run |
| Quality | Mutation rate, rollback rate | per-run |

---

## 5. Execution Priority

```
Phase 1 (P0):  Exp1 v3 (full sweep) → Exp2 + Exp3 (analytic from Exp1)
Phase 2 (P1):  Exp4 (algorithm variants) + Exp5 + Exp6 + Exp7
```

Phase 1 answers the main paper question (which solvers are evolvable, under which evolvers). Phase 2 deepens with algorithm interaction, capability attribution, cost, and dynamics.

---

## 6. Expected Contributions

1. **First evolvability benchmark**: systematic measurement of how much each of 9 LLMs improves from agent evolution, across 3 task domains and the 3 evolvers known to work.
2. **Solver–evolver pairing map**: explicit recommendation of which evolver to use for which solver, with same-family vs cross-family evidence.
3. **Evolvability scaling law** (preliminary): how solver size within a family relates to evolvability.
4. **Capability-to-evolvability mapping**: regression of evolvability on public benchmarks, as a predictor for practitioners.
5. **Cost-conditioned recommendations** for each solver (Pareto frontier over evolver × cycle count).
6. **Evolution dynamics library** — per-solver trajectories of skills, prompts, rollbacks across 3 benchmarks.

---

## 7. Related Work

- **RefineBench** (2024): LLM refinement ability — closely related to evolver role.
- **A-Evolve** (original): built the framework; did not study evolvability across solvers.
- **LMSYS Chatbot Arena**: general LLM ranking; no meta-cognitive angle.
- **SWE-bench, τ-bench, GAIA**: agent benchmarks on the solver axis only.

---

## 8. Detailed Experiment Plans

- [Exp1: Solver Evolvability Benchmark × Cross-Domain](exp1_core_evolver_benchmark.md)
- [Exp2: Solver × Evolver Interaction](exp2_solver_evolver_interaction.md)
- [Exp3: Solver Scaling Study for Evolvability](exp3_evolver_scaling.md)
- [Exp4: Algorithm × Evolvability Interaction](exp4_algorithm_evolver.md)
- [Exp5: Evolvability Capability Attribution](exp5_capability_attribution.md)
- [Exp6: Per-Solver Cost-Efficiency Frontier](exp6_cost_efficiency.md)
- [Exp7: Evolution Dynamics by Solver](exp7_evolution_dynamics.md)
