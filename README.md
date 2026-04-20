# A-Evolve 🧬: The Universal Infrastructure for Self-Improving Agents

[![GitHub stars](https://img.shields.io/github/stars/A-EVO-Lab/a-evolve?style=social)](https://github.com/A-EVO-Lab/a-evolve)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![arXiv](https://img.shields.io/badge/arXiv-2602.00359-b31b1b.svg)](https://arxiv.org/abs/2602.00359)

> **The PyTorch for Agentic AI.**
> A-Evolve is an open-source infrastructure that evolves *any* agent, across *any* domain, using *any* evolution algorithm — with zero human intervention.

[Quick Start](#quick-start) | [News](#news) | [Benchmark Highlights](#benchmark-highlights) | [Architecture & Design](#architecture--design) | [Contribution](#community--contributing)
</p>

![A-Evolve Teaser](figs/teaser.png)

---

## What Does A-Evolve Do?

You provide a Base Agent. A-Evolve returns a SOTA Agent. **3 lines of code. 0 hours of manual harness 
engineering.** One infra, any domain, any evolution algorithm.

```python
import agent_evolve as ae

evolver = ae.Evolver(agent="./my_agent", benchmark="swe-verified")
results = evolver.run(cycles=10)
```

### Benchmark Highlights

By applying our open-source **reference evolution algorithms** to a base Claude Opus-4.6 model with **zero manual harness engineering**, A-Evolve pushed agents into top-tier performance across four diverse benchmarks:

<table>
<tr>
<td align="center" width="23%">
<h3>🟢 MCP-Atlas</h3>
<img src="https://img.shields.io/badge/79.4%25-10b981?style=for-the-badge&labelColor=065f46" />
<br/><br/>
<strong>🥇 #1</strong><br/>
<sub>Baseline → <strong>79.4%</strong> (+3.4pp)</sub>
</td>
<td align="center" width="23%">
<h3>🔵 SWE-bench Verified</h3>
<img src="https://img.shields.io/badge/76.8%25-2563eb?style=for-the-badge&labelColor=1e3a5f" />
<br/><br/>
<strong>~#5</strong><br/>
<sub>Baseline → <strong>76.8%</strong> (+2.6pp)</sub>
</td>
<td align="center" width="23%">
<h3>🟣 Terminal-Bench 2.0</h3>
<img src="https://img.shields.io/badge/76.5%25-7c3aed?style=for-the-badge&labelColor=3b1d6e" />
<br/><br/>
<strong>~#7</strong><br/>
<sub>Baseline → <strong>76.5%</strong> (+13.0pp)</sub>
</td>
<td align="center" width="23%">
<h3>🟡 SkillsBench</h3>
<img src="https://img.shields.io/badge/34.9%25-d97706?style=for-the-badge&labelColor=78350f" />
<br/><br/>
<strong>#2</strong><br/>
<sub>Baseline → <strong>34.9%</strong> (+15.2pp)</sub>
</td>
</tr>
</table>

![A-Evolve Benchmarks](figs/a_evolve_benchmarks.png)

> *All results achieved with a single Claude Opus-4.6 base model, evolved using A-Evolve's sample algorithms. 0 hours of human harness engineering. Data checked March 2026.*

### News
- **04/20** **New Algorithm Drop**, A-Evolve added new evolutionary algorithm [GEPA](https://x.com/HenryL_AI/status/2046326722912739713?s=20), submitted by the [GEPA](https://gepa-ai.github.io/gepa/blog/) team.
- **04/10** **Integration**, A-Evolve is officially integrated into [Orch-Research Skills Library](https://x.com/HenryL_AI/status/2042688465855488476), along with others including AutoResearch, OpenRLHF, DeepSpeed, SGLang
- **04/07** **New Agent Drop**, We added recently leaked public ClawCode (Claude Code), took the evolution harness + skills we learned on Terminal-Bench 2.0 (TB2) and directly transplanted them onto the ClawCode. [Result](https://x.com/HenryL_AI/status/2041621538580132280) on TB2: baseline **67.8%** → **72.9%** (+5.1pp uplift)
- **04/03** **New Algorithm Drop**, A-Evolve added new evolutionary algorithm [Meta-Harness](https://x.com/HenryL_AI/status/2040218374458974715)
- **03/30** **Integration**, A-Evolve is officially integrated into [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw) 
- **03/25** 🚀 **Open-source A-Evolve**, the universal infrastructure for developing and testing evolving algorithms.
- **03/25** 📊 **Open-source 4 evolving algorithms** developed with A-Evolve, achieving SOTA **(#1, ~#5, ~#7, #2)** on MCP-Atlas, SWE-bench Verified, Terminal-Bench 2.0, and SkillsBench.
- **02/17** 📄 Release the official implementation of [*Position: Agentic Evolution is the Path to Evolving LLMs*](https://arxiv.org/abs/2602.00359) (arXiv 2602.00359).

We are evolving fast! Support our research by leaving a ⭐.

### What Does an Evolved Agent Look Like?

A-Evolve mutates real files in the workspace. Here's a before/after from our MCP-Atlas evolution:

<table>
<tr>
<th width="50%">Before (Seed Workspace)</th>
<th width="50%">After (Evolved — 79.4% on MCP-Atlas)</th>
</tr>
<tr>
<td>

```
mcp_agent/
├── manifest.yaml
├── prompts/system.md      ← 20 lines, generic
├── skills/                ← empty
└── memory/                ← empty
```

</td>
<td>

```
mcp_agent/
├── manifest.yaml
├── prompts/system.md      ← 20 lines, unchanged
├── skills/
│   ├── entity-verification/SKILL.md   ← NEW
│   ├── search-iteration/SKILL.md      ← NEW
│   ├── multi-requirement/SKILL.md     ← NEW
│   ├── code-execution/SKILL.md        ← NEW
│   └── conditional-handler/SKILL.md   ← NEW
└── memory/
    └── episodic.jsonl     ← 6 entries
```

</td>
</tr>
</table>

5 targeted skills outperformed 10 generic ones. Every mutation is git-tagged (`evo-1`, `evo-2`, …) for full reproducibility.

---

## Quick Start

### 1. Install

```bash
# PyPI (recommended)
pip install a-evolve              # core
pip install a-evolve[anthropic]   # Claude support
pip install a-evolve[mcp]         # MCP-Atlas benchmark
pip install a-evolve[swe]         # SWE-bench benchmark
pip install a-evolve[all]         # everything

# From source (for development)
git clone https://github.com/A-EVO-Lab/a-evolve.git && cd a-evolve
pip install -e ".[all,dev]"
```

### 2. Evolve — 3 Lines of Code

```python
import agent_evolve as ae

evolver = ae.Evolver(
    agent="swe-verified",           # built-in seed workspace (or path to yours)
    benchmark="swe-verified",       # built-in benchmark adapter
)
results = evolver.run(cycles=10)

print(f"Final score: {results.final_score:.3f}")
print(f"Converged:   {results.converged}")
```

A-Evolve ships with built-in seed workspaces (`swe`, `mcp`, `terminal`, `skillbench`) and benchmark adapters (`swe-verified`, `mcp-atlas`, `terminal-bench 2.0`, `skill-bench`). Point `agent=` at any of them — or at your own workspace directory.

### 3. Bring Your Own Agent (BYOA)

To make any agent evolvable, implement one method — `solve()`:

```python
from agent_evolve.protocol.base_agent import BaseAgent
from agent_evolve.types import Task, Trajectory

class MyAgent(BaseAgent):
    def solve(self, task: Task) -> Trajectory:
        return Trajectory(task_id=task.id, output="result")
```

Then evolve it:

```python
evolver = ae.Evolver(agent=MyAgent("./my_workspace"), benchmark="mcp-atlas")
results = evolver.run(cycles=10)
```

Your agent's evolvable state (prompts, skills, memory) lives as a standard directory — the [Agent Workspace](#the-agent-workspace-a-file-system-contract). A-Evolve mutates these files; your agent reloads. See [Architecture & Design](#architecture--design) for the full picture.

For benchmark-specific walkthroughs, see [SWE-bench Demo Guide](docs/swe-bench-demo.md), [MCP-Atlas Demo Guide](docs/mcp-atlas-demo.md), and [SkillBench Setup Guide](docs/skillbench-setup.md).

---

## Architecture & Design

![A-Evolve Framework](figs/A-EVOLVE-FRAMEWORK.png)

### The Agent Workspace: A File System Contract

A-Evolve's core insight: **all evolvable agent state lives on the file system as a standard directory structure.** This lets the evolution engine mutate any agent via LLM-driven file operations — without knowing the agent's internals.

```
my_agent/
├── manifest.yaml          # identity, entrypoint, evolvable layers
├── prompts/system.md      # system prompt
├── skills/                # SKILL.md files (dynamic skill library)
├── tools/                 # tool configurations
└── memory/                # episodic + semantic memory (JSONL)
```

The evolution engine reads these files, analyzes performance logs, and writes mutations back. The agent reloads. That's the entire contract.

### The Evolution Loop

Every cycle follows five phases:

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────┐    ┌────────┐
│  Solve  │───▶│ Observe │───▶│ Evolve  │───▶│ Gate │───▶│ Reload │
└─────────┘    └─────────┘    └─────────┘    └──────┘    └────────┘
```

1. **Solve** — Agent processes a batch of tasks (black-box execution).
2. **Observe** — Collect trajectories + benchmark feedback into structured logs.
3. **Evolve** — Evolution engine analyzes observations and mutates workspace files (prompts, skills, memory).
4. **Gate** — Validate mutations on holdout tasks. Regressed mutations are rolled back via git.
5. **Reload** — Agent reloads from the (possibly rolled-back) workspace.

The loop converges when EGL (Evolutionary Generality Loss) stabilizes or `max_cycles` is reached. Every accepted mutation is git-tagged (`evo-1`, `evo-2`, …), providing a full audit trail.

### Built-in Adapters

A-Evolve ships with ready-to-use benchmark adapters and seed workspaces:

| Adapter | Domain | Seed Workspace | Best Result |
| :--- | :--- | :--- | :--- |
| [`swe-verified`](docs/swe-bench-demo.md) | Real-world GitHub issues (Python repos) | `seed_workspaces/swe/` | **76.8%** (~#5) |
| [`mcp-atlas`](docs/mcp-atlas-demo.md) | Tool-calling via MCP (16+ servers) | `seed_workspaces/mcp/` | **79.4%** (🥇 #1) |
| [`terminal-bench`](docs/terminal-bench-demo.md) | Terminal/CLI ops in Docker | `seed_workspaces/terminal/` | **76.5%** (~#7) |
| [`skill-bench`](docs/skillbench-setup.md) | Agentic skill discovery | `seed_workspaces/skillbench/` | **34.9%** (~#2)|
| [`cl-bench`](examples/cl_bench_examples/) | Continual-learning rubric evaluation | — | **38.0%** |

### Pluggability: Bring Your Own Everything

A-Evolve is a **framework**, not a standalone agent. Every axis is pluggable:

| Axis | Interface | You Provide | Built-in Examples |
| :--- | :--- | :--- | :--- |
| **Agent (BYOA)** | `BaseAgent.solve()` | Any agent architecture — ReAct, Plan-and-Solve, custom | `SweAgent`, `McpAgent` |
| **Benchmark (BYOE)** | `BenchmarkAdapter.get_tasks()` / `.evaluate()` | Any domain with task + evaluation signal | SWE-bench, MCP-Atlas, Terminal-Bench 2.0, SkillsBench, CL-bench |
| **Algorithm (BYO-Algo)** | `EvolutionEngine.step()` | Any evolution strategy | `AEvolveEngine` (LLM-driven mutation) |
| **LLM Provider** | `LLMProvider.complete()` | Any model API | Anthropic, OpenAI, AWS Bedrock |

### Built-in Evolution Algorithms

A-Evolve ships with 4 reference evolution algorithms, each targeting different domains and strategies:

| Algorithm | Strategy | Best For | Docs |
| :--- | :--- | :--- | :--- |
| [`adaptive_evolve`](docs/algorithms/adaptive-evolve.md) | Per-claim feedback analysis + meta-learning | MCP-Atlas (🥇 #1, 79.4%) | [Guide](docs/algorithms/adaptive-evolve.md) |
| [`adaptive_skill`](docs/algorithms/adaptive-skill.md) | LLM-driven workspace mutation with bash tool access | Terminal-Bench 2.0 (~#7, 76.5%)  | [Guide](docs/algorithms/adaptive-skill.md) |
| [`skillforge`](docs/algorithms/skillforge.md) | LLM-driven workspace mutation with EGL gating | SkillsBench (#2, 34.9%) | [Guide](docs/algorithms/skillforge.md) |
| [`guided_synth`](docs/algorithms/guided-synth.md) | Memory-first evolution + LLM-guided intervention synthesis |  General-purpose, SWE-bench (~#5, 76.8%) | [Guide](docs/algorithms/guided-synth.md) |

#### Plugging in a custom evolution algorithm

Each algorithm lives in its own directory under `algorithms/`. Implement a single method:

```python
from agent_evolve.engine.base import EvolutionEngine
from agent_evolve.types import StepResult

class MyEvolutionEngine(EvolutionEngine):
    def step(self, workspace, observations, history, trial) -> StepResult:
        # Analyze observations, mutate workspace files, optionally run trial tasks
        ...
        return StepResult(accepted=True, score=new_score)
```

Then pass it to the Evolver:

```python
evolver = ae.Evolver(
    agent="swe-verified",
    benchmark="swe-verified",
    engine=MyEvolutionEngine(config),
)
```

The engine has full access to shared primitives — `TrialRunner` (on-demand validation), `EvolutionHistory` (observation + version queries), and `VersionControl` (git-based rollback) — but is never forced to use them. Minimal contract, maximum freedom.

---

## Community & Contributing

A-Evolve is built for the research community. We welcome contributions across every axis of the framework.

### For Algorithm Researchers

If you work in LLM self-optimization, reinforcement learning, or agent architectures — implement the `EvolutionEngine` interface and your algorithm instantly gains access to:

- Diverse environments (SWE-bench, MCP-Atlas, Terminal-Bench 2.0, SkillsBench, and more).
- Standardized agent workspace representations.
- Rigorous evaluation, gating, and logging infrastructure.

Drop your algorithm into `agent_evolve/algorithms/your_algo/` and open a PR.

### For Benchmark Authors

Implement `BenchmarkAdapter` to plug any new evaluation domain into A-Evolve. The interface is two methods: `get_tasks()` and `evaluate()`.

### Get Involved

- ⭐ **Star this repo** to support our research — we are evolving fast.
- 🐛 **[Open an issue](https://github.com/A-EVO-Lab/a-evolve/issues)** to report bugs or request features.
- 🔀 **[Submit a PR](https://github.com/A-EVO-Lab/a-evolve/pulls)** — new evolution algorithms, benchmark adapters, agent implementations, and documentation improvements are all welcome.
- 💬 **[Join our Discord]()** to discuss research directions, share results, and collaborate.

---

## Citation

If you use A-Evolve in your research, please cite our position paper:

```bibtex
@article{lin2026position,
  title={Position: Agentic Evolution is the Path to Evolving LLMs},
  author={Lin, Minhua and Lu, Hanqing and Shi, Zhan and He, Bing and Mao, Rui and Zhang, Zhiwei and Wu, Zongyu and Tang, Xianfeng and Liu, Hui and Dai, Zhenwei and others},
  journal={arXiv preprint arXiv:2602.00359},
  year={2026}
}
```

---

## License

[MIT](https://opensource.org/licenses/MIT)
