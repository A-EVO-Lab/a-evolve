Test with diverse query types relevant to temporal prediction:
- A financial data query (stock price, index value)
- A news/events query (election result, award winner)
- A sports query (match score, tournament result)
- A general knowledge query

For each, verify:
- Does it return useful, readable data? (not raw HTML or empty)
- Are all dates before the cutoff? (no future data leakage)
- Does it handle errors gracefully? (bad input → empty, not crash)
- Are results attributed? (source name + date for each result)
