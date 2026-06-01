Tasks span different prediction domains (sports, finance, politics, Chinese markets,
entertainment, science) with varying data source requirements. The temporal ordering
means available information and applicable reasoning strategies shift across the stream.
A search pipeline optimized for one domain may not cover another.

BRANCH PRUNING: Check the strategy tree's per-branch pass rate. If a branch's
cumulative pass rate is LOWER than main's rate on similar tasks, redirect those
tasks back to TARGET: main. A branch that consistently underperforms main is
actively hurting results — main's general tools work better than a weak specialist.
Only keep routing to a branch if it outperforms main on its target domains.
