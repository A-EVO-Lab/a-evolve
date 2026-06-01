Tasks are temporal prediction questions requiring factual information
available BEFORE a cutoff date. The solver calls web_search(query)
which returns results from the evolved search pipeline + Wikipedia +
web search fallbacks.

YOUR JOB: Identify DATA SOURCE gaps — domains where the pipeline
lacks structured APIs. Every gap you write MUST name a concrete data
domain, not a reasoning problem.

GOOD gap names (data domains): sports_scores_api_gap,
  box_office_data_gap, chinese_fund_price_gap, github_ranking_gap,
  steam_player_stats_gap, cricket_stats_gap, wta_rankings_gap
BAD gap names (reasoning problems): future_event_prediction_without_result,
  batch_deadline_cutoff, question_interpretation_error

For each batch, analyze:
1. Which tasks got NO structured API data (only news headlines)?
   → Name the missing data domain as a gap.
2. Which data domains have ZERO coverage in /solver_workspace/infra/?
   Read the source modules and list domains they DON'T cover.
3. Which existing API sources returned errors or empty results?
4. Are there regional/language gaps (Chinese, Japanese)?

ALSO: After analyzing failed tasks, audit the pipeline's OVERALL
domain coverage. Compare what it covers (read infra/sources/) vs
what the benchmark needs (sports, entertainment, finance, politics,
Chinese platforms, GitHub, gaming, prediction markets, climate,
commodities, rankings). Flag any uncovered domain as a gap even
if no task explicitly failed on it — future batches may need it.
