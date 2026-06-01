Tasks are temporal prediction questions. The solver calls web_search(query)
which returns results from the evolved search pipeline + fallbacks.

Every gap MUST name a DATA DOMAIN (e.g., sports_scores_api_gap,
box_office_data_gap, chinese_fund_price_gap), NOT a reasoning problem.
Research agents can find APIs and data sources — they cannot fix reasoning.

After analyzing failed tasks, also audit the pipeline's OVERALL domain
coverage by reading /solver_workspace/infra/sources/. Flag uncovered
domains even if no task explicitly failed on them.
