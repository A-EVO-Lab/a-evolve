FutureX-specific context for the analyst.

Domain vocabulary
- Tasks are temporal prediction questions across domains: sports,
  finance/economics, politics, entertainment, science, technology, and
  Chinese-platform rankings (Douban, QQ Music, NetEase, KolRank, etc.).
- Each task has a creation_date and resolution_date; under
  temporal-reveal, a task's label is visible only after its
  resolution_date is on or before the current batch watermark.
- The solver has no built-in web search — it calls the evolved
  pipeline at `infra/search_pipeline.py` via subprocess.

Common FutureX non-transferable artifacts to look for during the
transferability audit (framework Phase 1):
- "Stop searching after N queries" rules in `prompts/system.md` —
  helped early sports batches but break long-tail Chinese / niche
  rankings that need more queries.
- Sports-flavored reasoning baked into the prompt (odds → probability
  conversion) applied to non-sports domains.
- Memory entries that hard-code platform-specific routing (e.g., "for
  Polymarket Sports, use API X") — lock the system into one source
  family.
- Source files in `infra/sources/` that hard-code task IDs, dates, or
  competition names rather than parameterizing.
