PolyBench-specific context for the analyst.

Domain vocabulary
- Markets span event types (binary yes/no, multi-outcome,
  over/under thresholds) and domains (sports, crypto, politics,
  entertainment, news/current events).
- Stream is temporally ordered (timestamp / resolved_at metadata).
  Under temporal-reveal, a market's resolution is visible only after
  resolved_at <= the current batch watermark.
- Trade decisions are: BUY / SELL / SKIP, gated by a confidence
  threshold (default 0.6). The analyst should distinguish "skipped
  due to low confidence" from "traded incorrectly".

Common PolyBench non-transferable artifacts to look for during the
transferability audit (framework Phase 1):
- Hard-coded confidence thresholds tuned on one event type that
  cause over-skipping or over-trading on others.
- Decision rules in `prompts/system.md` that bake in domain heuristics
  (e.g., "for crypto, treat sentiment as predictive") that don't
  generalize to politics or sports.
- Tools or skills that hard-code specific market IDs, dates, or
  outcome formats rather than parameterizing.
- Memory entries citing single market resolutions without the
  underlying reasoning pattern.
