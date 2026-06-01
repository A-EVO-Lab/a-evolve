# Adaptive Auto-Harness

Code accompanying the paper **"Adaptive Auto-Harness: Sustained
Self-Improvement for Agentic System Deployment on Open-Ended Task
Streams"**.

The framework constructs and adapts an agent's *harness* (prompts,
skills, tools, memories, and supporting infrastructure) for
open-ended task streams. It contributes:

- A stateful multi-agent evolver that decomposes evolution into
  Analyst, parallel Researchers, Builder, and Verifier roles with
  cross-cycle memory and a temporal-reveal feedback gate.
- An adaptation operator over a harness tree, selecting a specialized
  branch per task at solve time.
- Two human-steering hooks for cases when stream history lacks the
  signal needed to build the next harness.
- Three benchmark adapters: PolyBench (prediction markets), CTF-Dojo
  (security challenges), and FutureX (event forecasting).

## Status

**Full code release is being prepared and will be published here
shortly.** The supplementary code is undergoing final review before
release.

## License

MIT.
