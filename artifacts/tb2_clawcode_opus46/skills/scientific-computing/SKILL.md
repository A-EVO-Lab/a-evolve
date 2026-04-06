---
name: scientific-computing
description: Strategies for scientific computing, numerical methods, bioinformatics/DNA tasks, logic circuit design, algorithmic challenges, and ML training tasks.
---

# Scientific Computing & Algorithmic Tasks

## CRITICAL: Write complete scripts to files for complex tasks
Multi-step scientific analysis MUST be written as a single .py file to avoid state loss:
```python
with open('/app/solve.py', 'w') as f:
    f.write("""#!/usr/bin/env python3
import numpy as np
# ... complete self-contained solution ...
""")
# Then run: bash("python3 /app/solve.py")
```

## Bioinformatics / DNA primer design
- Use `oligotm` CLI if available for Tm calculation (check with `which oligotm`)
- Use `primer3_core` if available for automated primer design
- Key Tm parameters: `-tp 1 -sc 1 -mv 50 -dv 2 -n 0.8 -d 500` (Santa Lucia, salt-corrected)
- Primer length: 15-30 bp, GC content 40-60%, Tm 55-65°C
- For Gibson/Golden Gate assembly: add overlaps/BsaI sites to 5' end of primers
- Parse FASTA files: handle multi-line sequences, strip whitespace
- Write results in FASTA format to the expected output path

## Logic circuit / hardware design
- Read simulator code FIRST to understand gate semantics and timing
- Key patterns: ripple-carry adder, mux, shift register
- For Fibonacci mod 2^N: doubling method or iterative with registers

## Numerical / distribution tasks
- For optimization: use scipy.optimize (fsolve, minimize, root)
- When searching distributions: verify KL divergence, entropy constraints
- Always validate: check sums to 1, non-negative, constraint satisfaction

## ML training tasks
- Check GPU: `python3 -c "import torch; print(torch.cuda.is_available())"`
- CPU-only: smaller batch size, simpler models, fewer epochs
- Save checkpoints frequently

## Verification
- Verify output format matches task specification exactly
- Save results to exact paths mentioned in task prompt
- Compare against reference values for correctness
