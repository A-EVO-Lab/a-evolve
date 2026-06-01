# Installation

## Requirements

- Python 3.11+
- Docker (for sandboxed evolver and CTF-Dojo task containers)
- 50+ GB free disk for benchmark data and trajectories
- One supported LLM provider (see below)

## Install

Unpack the supplement archive (e.g., `tar -xzf adaptive-auto-harness.tar.gz`),
enter the resulting directory, and install the package in editable mode:

```bash
cd adaptive-auto-harness
pip install -e ".[all]"
```

The `[all]` extra pulls every supported LLM provider and benchmark
backend. To install a minimal subset, replace `[all]` with one or
more of: `[bedrock]`, `[openai]`, `[litellm]`, `[swe]`, `[mcp]`,
`[skillbench]`, `[gepa]`.

## Environment variables

Copy `.env.template` to `.env` and fill in the values that apply to
your setup. The framework reads `.env` automatically.

### Required

| Variable          | Purpose                                                   |
|-------------------|-----------------------------------------------------------|
| `SOLVER_MODEL`    | Model ID used by the task-solving agent                   |
| `EVOLVER_MODEL`   | Model ID used by the evolver agents                       |

The placeholder strings `<solver-model-id>` and `<evolver-model-id>`
appear throughout the codebase. They are resolved at runtime from the
above env vars; replace them before running, or set the env vars and
the loader will substitute.

### Provider credentials (one of)

| Provider                | Required env vars                                       |
|-------------------------|---------------------------------------------------------|
| AWS Bedrock             | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` |
| OpenAI                  | `OPENAI_API_KEY`                                        |
| LiteLLM (any backend)   | provider-specific (see https://docs.litellm.ai/)        |

The default LLM client is in `agent_evolve/llm/`. To swap providers,
edit the `BedrockClient` or add a new client class implementing the
same interface; configs reference clients by name.

### Optional retrieval API keys (FutureX live mode)

| Variable           | Purpose                            |
|--------------------|------------------------------------|
| `TAVILY_API_KEY`   | Web search (Tavily)                |
| `FRED_API_KEY`     | Economic indicators (FRED)         |
| `SERPER_API_KEY`   | Web search (Serper)                |
| `JINA_API_KEY`     | Web search (Jina)                  |
| `EXA_API_KEY`      | Web search (Exa)                   |

Without these, FutureX runs in strict mode using only Wikipedia
revisions plus DuckDuckGo with date filtering.

## Data

Datasets are not shipped with this repository. From the project
root:

```bash
python data/download_data.py --benchmark polybench    # ~40 MB
python data/download_data.py --benchmark ctf_dojo     # ~2 GB
python data/download_data.py --benchmark futurex      # ~10 MB
```

See `data/README.md` for the source of each benchmark and any
manual prerequisites (e.g., CTF-Dojo Docker images).

## Verifying installation

```bash
python -c "import agent_evolve; print(agent_evolve.__file__)"
pytest tests/ -q
```

A successful test run validates the framework imports, contract
helpers, and a small evolution dry-run.
