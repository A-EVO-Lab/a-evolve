FutureX-specific context for the researcher — full system (with branching).

This run uses git branches; your research records drive whether the
builder commits a fix to main or to a specialized branch. For each
candidate source you record, indicate transferability:

For each approach you test, document:
- TRANSFERABILITY: which FutureX domains besides "{regime}" would this
  source help, hurt, or be neutral on? A source that returns highly
  domain-specific data (e.g., a Chinese music chart API) is best
  isolated to a domain branch; a source with broad coverage
  (e.g., Wikipedia) belongs on main.
- Each research record SHOULD include:
    transferable: <true|false>
    helps_categories: [list of domains]
    hurts_categories: [list]
    recommended_target: <main | branch/{regime}>
  Sources that help multiple domains without harm go to main; sources
  that only help "{regime}" go to branch/{regime}.

The pipeline needs data sources for temporal prediction tasks. The
research output is a set of JSON-Lines records describing APIs / RSS
feeds / structured endpoints that the builder can wire into
`infra/sources/`.

Concrete source families worth investigating (factual reference,
not exhaustive):
- Sports: ESPN APIs, TheSportsDB, football-data.org, cricinfo,
  team-specific official APIs.
- Entertainment: TMDB, Box Office Mojo, streaming charts, Spotify
  Charts API, Last.fm.
- Financial / economic data: Yahoo Finance v8, FRED, Alpha Vantage,
  Eastmoney, Sina Finance.
- Prediction markets: Manifold, Polymarket, Kalshi, Metaculus.
- Chinese platforms: Douban, Maoyan, Bilibili, KolRank, QQ Music,
  NetEase Cloud Music, Sina, Eastmoney HSGT.

Per-source evaluation criteria (use as checks when filling out the
research record's `works`, `coverage`, `latency_ms`, `does_not_cover`):
1. Can it return data for a specific past date (cutoff_date support)?
2. Is the response parseable with Python stdlib (JSON / XML / CSV
   strongly preferred over HTML scraping)?
3. Is it reliable (3/3 calls succeed in under ~8s)?
4. Does it complement existing sources, or duplicate one?

Interface constraints to keep in mind: the resulting code under
`infra/` runs in the SOLVER's sandbox (Python stdlib only, ~20s budget,
must produce valid JSON on stdout). Don't research libraries the
builder cannot ship.
