"""
DuckDuckGo + htmldate search with publication-date filtering.

Drop-in replacement for Tavily in the FutureX strict search pipeline.
Searches with DuckDuckGo (free), fetches each result's HTML, extracts the
publication date with htmldate, and drops anything published after the cutoff.

Usage:
    results = htmldate_filtered_search(
        query="AFCON 2025 predictions",
        cutoff_date="2025-01-08",
        max_results=5,
    )

Dependencies:
    pip install htmldate duckduckgo-search requests
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# File-based lock for DDGS — ensures only one DDGS call at a time across
# ALL threads in ALL workers (ThreadPoolExecutor shares process space).
# The threading.Lock is fast but only works within one process; the file
# lock coordinates across any callers in the same filesystem.
import fcntl as _fcntl
import tempfile as _tempfile

_DDGS_LOCKFILE = os.path.join(_tempfile.gettempdir(), "aevolve_ddgs.lock")
_DDGS_THROTTLE = 3.0  # seconds between DDGS calls


def _ddgs_search(query: str, max_results: int = 10, timelimit: Optional[str] = None) -> list:
    """Run DDGS search in a subprocess to isolate its thread pool.

    Uses a file-based lock so only one DDGS call runs at a time across all
    concurrent workers, preventing DuckDuckGo rate-limiting.
    """
    # Acquire file lock — blocks until no other worker is searching
    lock_fd = open(_DDGS_LOCKFILE, "w")
    try:
        _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
        time.sleep(_DDGS_THROTTLE)  # always wait between calls

        tl_arg = f"timelimit={timelimit!r}," if timelimit else ""
        code = (
            "import json; from ddgs import DDGS; "
            f"r=list(DDGS(timeout=15).text({query!r},"
            f"{tl_arg}"
            f"max_results={max_results},"
            f"backend='auto')); "
            "print(json.dumps(r))"
        )
        # Retry once on empty result (DDGS sometimes rate-limits silently)
        for attempt in range(2):
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=25,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                results = json.loads(proc.stdout.strip())
                if results:
                    return results
            if attempt == 0:
                time.sleep(_DDGS_THROTTLE)  # extra wait before retry
        if proc.stderr:
            logger.debug("DDGS stderr for %r: %s", query[:40], proc.stderr[:100])
        return []
    except Exception as e:
        logger.warning("DDGS search failed for %r: %s", query[:40], e)
        return []
    finally:
        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
        lock_fd.close()


def _fetch_html(url: str, timeout: float = 8.0) -> Optional[str]:
    """Fetch HTML content from a URL.

    Returns None on any error (timeout, 4xx/5xx, connection refused, etc.).
    """
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; AdaptiveAutoHarness/1.0; "
                    "+temporal-prediction-benchmark)"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return None
        # Only process HTML responses
        ct = resp.headers.get("Content-Type", "")
        if "html" not in ct and "text" not in ct:
            return None
        return resp.text
    except Exception:
        return None


def _extract_date(html: str, url: str = "") -> Optional[str]:
    """Extract publication date from HTML using htmldate.

    Args:
        html: Raw HTML string.
        url: Original URL (used by htmldate for URL-based date hints).

    Returns:
        Date string in YYYY-MM-DD format, or None if not extractable.
    """
    try:
        from htmldate import find_date
        return find_date(
            html,
            url=url,
            extensive_search=False,
            original_date=True,  # prefer original publication over last-modified
            outputformat="%Y-%m-%d",
        )
    except ImportError:
        logger.error(
            "htmldate not installed. Install with: pip install htmldate"
        )
        return None
    except Exception as e:
        logger.debug("htmldate extraction failed for %s: %s", url, e)
        return None


def _is_before_cutoff(date_str: Optional[str], cutoff: str) -> Optional[bool]:
    """Check if a date string is strictly before the cutoff.

    Returns:
        True if date < cutoff, False if date >= cutoff, None if date is None.
    """
    if not date_str:
        return None
    try:
        return date_str < cutoff
    except Exception:
        return None


def htmldate_filtered_search(
    query: str,
    cutoff_date: str,
    max_results: int = 5,
    fetch_timeout: float = 8.0,
    fetch_workers: int = 4,
    ddgs_timelimit: Optional[str] = None,
    snippet_filter_fn=None,
) -> List[dict]:
    """Search DuckDuckGo and filter results by publication date using htmldate.

    This is the main entry point. It:
    1. Searches DuckDuckGo for the query
    2. Fetches HTML for each result URL (concurrently)
    3. Extracts publication dates with htmldate
    4. Drops results published on or after cutoff_date
    5. For results where htmldate returns None, applies snippet_filter_fn

    Args:
        query: Search query string.
        cutoff_date: YYYY-MM-DD string. Results on or after this date are dropped.
        max_results: Maximum results to return after filtering.
        fetch_timeout: Timeout per HTTP fetch in seconds.
        fetch_workers: Number of concurrent fetch threads.
        ddgs_timelimit: Optional DDGS time filter (e.g. "m", "y").
        snippet_filter_fn: Optional callable(text) -> bool that returns True if
            the snippet text appears to be after the cutoff. Used as a fallback
            when htmldate cannot extract a date.

    Returns:
        List of dicts with keys:
            title, url, snippet, published_date (str|None), source_domain,
            date_source ("htmldate"|"url_pattern"|"passed_heuristic"|"no_date")
    """
    # Step 1: DDGS search (fetch more than needed since some will be filtered)
    fetch_count = max(max_results * 3, 10)
    raw_results = _ddgs_search(query, max_results=fetch_count, timelimit=ddgs_timelimit)
    if not raw_results:
        return []

    # Step 2+3: Fetch HTML and extract dates concurrently
    def _process_one(r: dict) -> dict:
        url = r.get("href", "")
        title = r.get("title", "")
        snippet = r.get("body", "")
        domain = ""
        try:
            domain = urlparse(url).netloc
        except Exception:
            pass

        result = {
            "title": title,
            "url": url,
            "snippet": snippet,
            "published_date": None,
            "source_domain": domain,
            "date_source": "no_date",
        }

        if not url:
            return result

        # Try URL pattern first (fast, no fetch needed)
        url_date = _extract_date_from_url(url)
        if url_date:
            check = _is_before_cutoff(url_date, cutoff_date)
            if check is False:
                result["published_date"] = url_date
                result["date_source"] = "url_pattern_blocked"
                return result
            # URL date passes — still try htmldate for more accurate date
            result["published_date"] = url_date
            result["date_source"] = "url_pattern"

        # Fetch HTML and extract date
        html = _fetch_html(url, timeout=fetch_timeout)
        if html:
            extracted = _extract_date(html, url=url)
            if extracted:
                result["published_date"] = extracted
                result["date_source"] = "htmldate"
                # htmldate overrides URL pattern date (more accurate)

        return result

    results = []
    # Guard every executor.submit/as_completed path against interpreter
    # shutdown. The outer caller may have been abandoned by a batch
    # deadline; if so, atexit is already running and submit() raises
    # RuntimeError("cannot schedule new futures after interpreter
    # shutdown"). Bail gracefully instead of surfacing the warning.
    try:
        with ThreadPoolExecutor(max_workers=fetch_workers) as pool:
            try:
                futures = {pool.submit(_process_one, r): r for r in raw_results}
            except RuntimeError:
                return []
            try:
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception:
                        pass
            except RuntimeError:
                return results
    except RuntimeError:
        return results

    # Preserve original search ranking
    url_order = {r.get("href", ""): i for i, r in enumerate(raw_results)}
    results.sort(key=lambda r: url_order.get(r["url"], 999))

    # Step 4: Filter by cutoff date
    filtered = []
    n_kept_dated = 0
    n_kept_undated = 0
    n_dropped_date = 0
    n_dropped_heuristic = 0
    for r in results:
        pub = r["published_date"]
        check = _is_before_cutoff(pub, cutoff_date)

        if check is False:
            # Date is on or after cutoff — drop
            n_dropped_date += 1
            logger.debug(
                "Dropped (date=%s >= cutoff=%s): %s",
                pub, cutoff_date, r["url"],
            )
            continue

        if check is True:
            # Date is before cutoff — keep
            n_kept_dated += 1
            filtered.append(r)
            continue

        # No date extracted — fall back to snippet heuristic
        if snippet_filter_fn and snippet_filter_fn(
            f"{r['title']} {r['snippet']}"
        ):
            n_dropped_heuristic += 1
            logger.debug("Dropped (snippet heuristic): %s", r["url"])
            continue

        n_kept_undated += 1
        r["date_source"] = "passed_heuristic"
        filtered.append(r)

    logger.info(
        "filter: %d fetched → %d kept (%d dated + %d undated), "
        "%d dropped (date>=%s: %d, heuristic: %d)",
        len(results), len(filtered), n_kept_dated, n_kept_undated,
        n_dropped_date + n_dropped_heuristic, cutoff_date,
        n_dropped_date, n_dropped_heuristic,
    )
    return filtered[:max_results]


def _extract_date_from_url(url: str) -> Optional[str]:
    """Fast regex extraction of dates from URL paths.

    Catches patterns like /2024/03/15/ or /2024-03-15- in URLs.
    Returns YYYY-MM-DD string or None.
    """
    # Match /YYYY/MM/DD/ or /YYYY-MM-DD
    m = re.search(r'/(\d{4})[/-](\d{2})[/-](\d{2})(?:[/-]|$)', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1995 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def format_for_agent(
    results: List[dict],
    query: str,
    cutoff_str: str,
) -> str:
    """Format htmldate search results for the FutureX agent.

    Produces the same text format as the existing _strict_search Tavily output
    so the agent sees a consistent interface.

    Args:
        results: Output from htmldate_filtered_search().
        query: Original search query.
        cutoff_str: YYYY-MM-DD cutoff string (for display).

    Returns:
        Formatted string for the agent's web_search tool response.
    """
    if not results:
        return f"No pre-cutoff web content for '{query}'"

    lines = [f"Results for '{query}' (pre-cutoff, before {cutoff_str}):"]
    for i, r in enumerate(results, 1):
        pub = r.get("published_date", "")
        source = f"[Web {pub}]" if pub else "[Web]"
        title = r.get("title", "").strip()
        snippet = r.get("snippet", "").strip()
        lines.append(f"{i}. {title}\n   {source} {snippet}")

    return "\n".join(lines)
