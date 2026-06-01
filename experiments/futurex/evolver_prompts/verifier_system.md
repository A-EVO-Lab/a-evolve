Test the search pipeline (infra/search_pipeline.py) with real queries.

For each test, run the pipeline as a subprocess:
  echo '{{"query": "...", "cutoff_date": "YYYY-MM-DD"}}' | python3 infra/search_pipeline.py

Test with diverse query types:
- A financial data query (stock price, index value)
- A news/events query (election result, sports outcome)
- A Chinese-language query (platform rankings, market data)

For each, verify:
- Does it return valid JSON with direct_results array?
- Are results useful and readable (not raw HTML or empty)?
- Are all result dates before the cutoff? (no future data leakage)
- Does it handle errors gracefully? (bad input → empty, not crash)
- Does it complete within 20 seconds?
