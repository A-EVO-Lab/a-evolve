# A-Evolve Visual UI

Visual frontend for `agentic-evolution-private` to inspect the self-evolve process:

- Browse sessions and batches
- Inspect per-task trajectory and failure points
- View 4-stage evolve pipeline (diagnosis -> plan -> apply -> verify)
- Compare vanilla vs evolved performance
- Inspect generated tools/skills/knowledge/patches

## One-click startup

From `a-evolve-ui`:

```bash
npm run dev:one-click
```

This command will:

1. Install frontend dependencies if missing
2. Install backend requirements
3. Start backend on `http://localhost:8000`
4. Start frontend on `http://localhost:5173`

Press `Ctrl+C` once to stop both services.

## Manual startup

### Backend

```bash
cd server
python3 -m pip install -r requirements.txt
python3 -m uvicorn main:app --reload --port 8000
```

### Frontend

```bash
npm install
npm run dev
```

## API data source paths

By default, backend reads:

- Sessions: `agentic-evolution-private/tools_sandbox/sessions`
- Experiments: `agentic-evolution-private/experiments/outputs`

You can override with environment variables:

- `AEVOLVE_SESSIONS_DIR`
- `AEVOLVE_EXPERIMENTS_DIR`

Example:

```bash
AEVOLVE_SESSIONS_DIR=/path/to/sessions AEVOLVE_EXPERIMENTS_DIR=/path/to/outputs python3 -m uvicorn main:app --reload --port 8000
```

## Troubleshooting

- If `npm` or `node` fails to run, fix local Node installation first.
- If `uvicorn` is missing, install backend requirements:
  `python3 -m pip install -r server/requirements.txt`
- If there are no sessions listed in UI, verify the sessions directory exists and contains run artifacts.
