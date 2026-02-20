# A-Evolve: Self-Evolving Agent Framework

A framework for building self-evolving agents that improve through interaction-time evolution. The core architecture follows an **Observe -> Propose -> Update** pipeline where agents learn from experience and iteratively improve their capabilities.

## Core Principles

- **Modular**: Observer, Proposer, and Updater are independent, extensible modules
- **Multi-Provider**: Supports Claude, GPT, and Gemini as LLM backends
- **Tool-Enabled**: Agents can create and evolve tools autonomously
- **Safe Evolutions**: Updates return new agent instances, enabling rollback and branching
- **Persistent State**: Evolution state (tools, skills, knowledge) persists across sessions

## Architecture

```
Input ─────────▶│   Agent     │───────► Output
                └──────┬─────┘
                       │
                       ▼
                ┌────────────┐
                │  Observer   │  (AppWorldObserver, PersistentObserver)
                └──────┬─────┘
                       ▼
                ┌────────────┐
                │  Proposer   │  (ToolEnabledProposer: Diagnose → Plan → Apply → Verify)
                └──────┬─────┘
                       ▼
                ┌────────────┐
                │   Updater   │  (HybridToolPatchUpdater)
                └──────┬─────┘
                       ▼
                new_agent_instance
```

## Directory Structure

```
a-evolve/
    README.md
    requirements.txt
    agent/
        agent.py                    # Base Agent class
        agent_factory.py            # Agent factory
        claude_agent.py             # Anthropic Claude agent
        gpt_agent.py                # OpenAI GPT agent
        gemini_agent.py             # Google Gemini agent
        tool_enabled_agent.py       # Base tool-enabled agent
        tool_enabled_claude_agent.py
        tool_enabled_gpt_agent.py
        tool_enabled_gemini_agent.py
    modules/
        observer/
            observer.py             # Base Observer interface
            persistent_observer.py  # JSONL file-based logging
            appworld_observer.py    # AppWorld benchmark observer
        proposer/
            proposer.py             # Base Proposer interface
            tool_enabled_proposer.py    # 4-stage coding agent proposer
            hybrid_tool_patch_proposer.py  # Combines tools + patches
            ablation_proposer.py    # For ablation experiments
            analysis_tools.py       # Historical observation analysis
            proposer_agent/
                diagnosis.py        # Stage 1: Error analysis
                appworld_diagnosis.py  # AppWorld-specific diagnosis
                plan.py             # Stage 2: Plan generation
                apply.py            # Stage 3: Apply changes
                verifier.py         # Stage 4: Verification
        updater/
            updater.py              # Base Updater interface
            hybrid_tool_patch_updater.py  # Tool + patch updater
            appworld_updater.py     # AppWorld state persistence
        tools/
            tool_workspace.py       # Isolated tool environments
            tool_registry.py        # Tool registration & discovery
            tool_builder.py         # Tool code generation
            tool_executor.py        # Sandboxed tool execution
        evolver/
            evolution_state.py      # Persistent state St = {Tt, Mt, Kt}
            meta_evolver.py         # Meta-evolution framework
        skills/
            skill_loader.py         # SKILL.md loader
    examples/
        run_appworld_claude_0115.py       # Main entry point (Claude)
        run_appworld_claude.py            # Claude runner (shared utils)
        run_appworld_gpt.py               # Multi-provider runner (GPT/Gemini/Claude)
        appworld_agent_system_prompt.py   # Shared prompt utilities
        recompute_metrics.py              # Metric recomputation tool
    tests/
        test_hybrid_system.py
        test_proposer_improvements.py
        test_multiline_tool_call_parsing.py
        test_execution_trace.py
        test_critical_fixes.py
        test_copy_session.py
        test_tool_building_system.py
        test_registry_fix.py
        demo_tool_building.py
```

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

### Running AppWorld Experiments

```bash
# Main experiment (Claude Haiku as agent, Claude Sonnet as proposer)
bash examples/run_appworld.sh

# Multi-provider (GPT/Gemini)
bash examples/run_appworld_gpt.sh
bash examples/run_appworld_gemini.sh
```

### Supported Models

- **Anthropic Claude**: `claude-haiku-4-5-20251001`, `claude-sonnet-4-20250514`, `claude-sonnet-4-5-20250929`
- **OpenAI GPT**: `gpt-5`, `gpt-5-mini`
- **Google Gemini**: `gemini-3-flash-preview`

## Extending the Framework

### Custom Observer

```python
from modules.observer import Observer

class CustomObserver(Observer):
    def observe(self, agent, input_text, output_text):
        return {
            "input": input_text,
            "output": output_text,
            "reward": calculate_reward(output_text),
        }
```

### Custom Proposer

```python
from modules.proposer import Proposer

class CustomProposer(Proposer):
    def propose(self, agent, obs, context=None):
        return {
            "type": "hybrid",
            "action": "ADD_PATCH",
            "patch_spec": {"patch": "...", "hypothesis": "..."}
        }
```

### Custom Updater

```python
from modules.updater import Updater

class CustomUpdater(Updater):
    def update(self, agent, proposal):
        # Always return a NEW agent instance
        new_agent = Agent(...)
        return new_agent
```

## Design Notes

- The `Agent` class is minimal and knows nothing about observation/training
- `state` is generic and can include memory, parameters, etc.
- `Update` is functional: it never mutates the original agent
- Observation and Proposal objects must be JSON serializable
- The Proposer operates as a 4-stage coding agent: Diagnose -> Plan -> Apply -> Verify
