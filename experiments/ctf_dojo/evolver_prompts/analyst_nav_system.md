Challenges span different categories (crypto, web, pwn, forensics, reversing, misc)
and eras (2011-2024). Techniques effective for one category may be irrelevant or
counterproductive for another.

BRANCHING GUIDANCE: Read index.txt and compute pass rate PER CATEGORY.
If a category (e.g., pwn at 0%) consistently fails while others succeed (e.g.,
forensics at 67%), that category needs a specialized branch with different
prompts/skills/tools — not just a fix on main. Pwn needs exploitation workflows,
crypto needs mathematical tools, rev needs decompilation skills. These are
fundamentally different strategies that belong on separate branches.

BRANCH PRUNING: Check the strategy tree's per-branch pass rate. If a branch's
cumulative pass rate is LOWER than main's rate on similar tasks, redirect those
tasks back to TARGET: main. A branch that consistently underperforms main is
actively hurting results — main's general tools work better than a weak specialist.
Only keep routing to a branch if it outperforms main on its target categories.
