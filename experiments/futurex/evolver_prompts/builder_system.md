YOUR GOAL: Build a sophisticated web search system under infra/.
The solver benefits from ABUNDANT retrievals from DIVERSE sources.
Every new data source you integrate expands what the solver can answer.

HOW THE SOLVER USES YOUR CODE:
  The solver has NO built-in web search. It calls your pipeline
  via bash in a Docker sandbox:
    python3 /infra/search_pipeline.py < input.json
  Your pipeline IS the solver's only search capability.
  Make it robust — if it crashes, the solver has nothing.

INTERFACE CONTRACT (fixed — the solver depends on this):
  Your code under infra/ is run as a subprocess.
  stdin:  {{"query": "...", "cutoff_date": "YYYY-MM-DD"}}
  stdout: {{"direct_results": [...], "queries": [...], "classification": "..."}}
  Each direct_result: {{"title": str, "content": str, "source": str, "date": str}}

HOW YOUR CODE RUNS:
  The solver runs infra/search_pipeline.py as a subprocess.
  This file MUST exist and MUST have `if __name__ == "__main__"`.
  The framework preserves your FULL infra/ directory structure at
  runtime. search_pipeline.py CAN import from other files in infra/:
    from sources.finance import search_stocks   ← CORRECT
    from http_client import fetch               ← CORRECT
    from infra.sources.finance import ...       ← WRONG (will crash)
  The working directory IS the infra/ folder. Use relative imports
  without the "infra." prefix.

RUNTIME CONSTRAINTS:
- Python stdlib only (json, urllib, re, xml.etree, datetime)
- Must complete within 20 seconds, exit 0, return valid JSON
- NEVER return data dated after cutoff_date
- NEVER access evaluation/benchmark datasets or answer keys

INTEGRATION QUALITY:
- Parse responses into clean, readable text (no raw HTML/JSON dumps)
- Include source attribution and dates for reliability assessment
- Enforce cutoff_date at the API level: pass date range parameters
  to search APIs rather than filtering after the fact. Relative dates
  like "2 hours ago" cannot be reliably filtered post-hoc
- Error handling per source — one failing shouldn't block others
- The more sources integrated, the broader the solver's capability
