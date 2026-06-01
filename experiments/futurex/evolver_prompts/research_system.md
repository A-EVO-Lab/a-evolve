The pipeline needs data sources for temporal prediction tasks. Focus on
discovering APIs that return structured, date-filtered data.

DISCOVER SOURCES BY EXPLORING THE WEB:
- Search for public APIs, RSS feeds, and structured data endpoints
- For sports: ESPN APIs, TheSportsDB, football-data.org, cricinfo
- For entertainment: TMDB, Box Office Mojo, streaming charts
- For financial data: Yahoo Finance v8, FRED, Alpha Vantage, Eastmoney
- For prediction markets: Manifold, Polymarket, Kalshi, Metaculus APIs
- For Chinese platforms: Douban, Maoyan, Eastmoney, Bilibili, KolRank

For each source, evaluate:
1. Can it return data for a specific past date?
2. Is the response parseable with Python stdlib? (JSON/XML/CSV >> HTML)
3. Does it work reliably? (3/3 calls succeed, <8s each)
4. Does it complement what already exists in infra/sources/?

ALSO EXPLORE BEYOND YOUR ASSIGNED REGIME:
After investigating your assigned gap, use remaining time to discover
sources in domains NOT yet covered by the pipeline.
