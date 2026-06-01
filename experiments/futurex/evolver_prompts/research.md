Discover as many useful data sources as possible. The value comes
from BREADTH — finding sources the previous cycles missed.

EXPLORE YOUR SANDBOX ENVIRONMENT:
Before searching externally, check what's already available in the
sandbox: run `env | grep API` and `ls /solver_workspace/` to discover
any pre-configured API keys or reference implementations. These can
unlock high-quality search and data-access tools.

DISCOVER SOURCES BY EXPLORING THE WEB:
- Search for public APIs, open data portals, and RSS feeds relevant
  to your assigned regime. Try queries like:
  "site:github.com <domain> API python", "<domain> public API JSON",
  "<platform> RSS feed", "<domain> open data"
- Browse GitHub repos that solve similar search/data-access problems.
  Study their code to learn what APIs exist and how to call them.
- For Chinese platforms (Douban, Maoyan, Eastmoney, Bilibili, KolRank),
  search for working scraping examples with the right headers/cookies.
- For sports data: ESPN APIs, TheSportsDB, football-data.org, cricinfo
- For entertainment: TMDB, Box Office Mojo, IMDB, streaming charts
- For financial data: Yahoo Finance v8, FRED, Alpha Vantage, Eastmoney
- For prediction markets: Manifold, Polymarket, Kalshi, Metaculus APIs

ALSO EXPLORE BEYOND YOUR ASSIGNED REGIME:
After investigating your assigned gap, use remaining time to discover
sources in domains NOT yet covered by the pipeline. Read the current
infra/sources/ to identify what's missing, then test APIs for those
uncovered domains. Even 2-3 records for an adjacent domain help the
builder expand coverage in the next cycle.

For each source, evaluate:
1. What query types does it cover? What does it NOT cover?
2. Can it return data for a specific past date?
3. Is the response parseable with stdlib? (JSON/XML/CSV >> HTML)
4. Does it work reliably? (3/3 calls succeed, <8s each)
5. How does it complement other sources in this regime?

Multiple sources per regime means fallback chains and broader coverage.
