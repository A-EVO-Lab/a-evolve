Markets in this benchmark span different event types (binary yes/no, multi-outcome,
over/under thresholds), different domains (sports, crypto, politics, entertainment),
and different information availability levels. The task stream is temporally ordered —
market characteristics shift over time. A strategy optimized for one market type
may underperform on another.

BRANCH PRUNING: Check the strategy tree's per-branch pass rate. If a branch's
cumulative pass rate is LOWER than main's rate on similar tasks, redirect those
tasks back to TARGET: main. A branch that consistently underperforms main is
actively hurting results — main's general tools work better than a weak specialist.
Only keep routing to a branch if it outperforms main on its target domains.
