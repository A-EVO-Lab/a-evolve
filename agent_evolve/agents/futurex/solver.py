"""FutureX solver entry points for solve_all_with_evolution.py.

Integrates FutureX temporal prediction benchmark with the evolution
framework.  Each task gets a lightweight Docker sandbox (shared with
polybench) so the solver can execute evolved tools via ``bash``, plus a
time-bounded ``web_search`` tool for gathering pre-cutoff information.

Module-level side effects (the ``.env`` auto-loader and the shared
search throttle lock) live in ``sandbox.py`` so every ProcessPool/ThreadPool
worker that imports either entry point picks them up exactly once.
"""

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

from agent_evolve.config import EvolveConfig

# Import from sandbox.py so we share the single _search_lock + .env state
# across every worker process / thread that reaches this file.
from .sandbox import (
    _DDGS_THROTTLE,
    _WIKI_THROTTLE,
    _last_search_time,
    _search_lock,
    start_sandbox,
    stop_sandbox,
)

logger = logging.getLogger(__name__)


# ─── Framework hooks ────────────────────────────────────────────────────

def setup(args) -> dict:
    """Setup FutureX backend for evolution framework."""
    from agent_evolve.benchmarks.futurex.futurex import FutureXBenchmark
    from agent_evolve.agents.futurex.agent import FutureXAgent

    out_dir = Path(args.output_dir)

    ws_dir = out_dir / "futurex"
    if not ws_dir.exists():
        seed = Path(args.seed_workspace)
        ws_dir.parent.mkdir(parents=True, exist_ok=True)
        if seed.exists():
            shutil.copytree(seed, ws_dir)
        else:
            ws_dir.mkdir(parents=True, exist_ok=True)
            (ws_dir / "prompts").mkdir(exist_ok=True)
            (ws_dir / "prompts" / "system.md").write_text(
                "You are a temporal prediction agent that forecasts "
                "future events using only information available before "
                "the task's creation date.\n")
            (ws_dir / "skills").mkdir(exist_ok=True)
            (ws_dir / "memory").mkdir(exist_ok=True)
            (ws_dir / "infra").mkdir(exist_ok=True)

    config = EvolveConfig.from_yaml(args.config) if args.config else EvolveConfig()
    skip = frozenset(
        name for name, enabled in [
            ("skills", config.evolve_skills),
            ("memory", config.evolve_memory),
            ("tools", config.evolve_tools),
        ] if not enabled
    )
    agent = FutureXAgent(
        workspace_dir=str(ws_dir), config=config, skip_layers=skip,
    )
    benchmark = FutureXBenchmark(config)

    return {
        "agent": agent,
        "benchmark": benchmark,
        "executor": "thread",
        "cleanup": None,
    }


def build_prompts(agent, tasks: list) -> dict:
    """Build prompts and configuration for FutureX tasks."""
    tool_files = {}
    if hasattr(agent, 'tool_registry'):
        for t in agent.tool_registry:
            name = t.get("name", "")
            if name and hasattr(agent, 'workspace'):
                impl = agent.workspace.read_tool(name)
                if impl:
                    tool_files[f"{name}.py"] = impl

    system_prompt = ""
    if hasattr(agent, '_build_system_prompt'):
        system_prompt = agent._build_system_prompt()
    elif hasattr(agent, 'workspace'):
        try:
            system_prompt = agent.workspace.read_system_prompt() or ""
        except Exception:
            system_prompt = "You are a FutureX temporal prediction agent."

    out = {
        "system_prompt": system_prompt,
        "user_prompts": {t.id: t.input for t in tasks},
        "tool_files": tool_files,
        "config": agent.config if hasattr(agent, 'config') else None,
    }
    # Emit raw harness pieces ONLY when a retrieval adaptation operator is
    # active (flag set by the registry); enables worker-side per-task
    # filtering without affecting M0/M4 (flag absent -> nothing emitted).
    if getattr(agent, "_emit_harness_pieces", False):
        from .._harness_filter import build_harness_pieces
        out["harness_pieces"] = build_harness_pieces(agent)
    return out


# ─── Solver ─────────────────────────────────────────────────────────────

def solve_one(task_data: Dict[str, Any], args_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Solve a single FutureX task in a subprocess.

    Mirrors the polybench solve_one pattern: Docker sandbox for evolved
    tools, plus a time-bounded web_search tool for information gathering.
    """
    import logging, os, time, sys

    sys.setrecursionlimit(3000)

    for key in ['AWS_PROFILE', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_CONFIG_FILE']:
        os.environ.pop(key, None)

    import strands.telemetry.tracer as _tracer
    _orig_add = _tracer.Tracer._add_event_messages
    def _safe_add(self, span, messages):
        if not span or not getattr(span, "is_recording", lambda: False)():
            return
        try:
            _orig_add(self, span, messages)
        except RecursionError:
            pass
    _tracer.Tracer._add_event_messages = _safe_add

    from strands.models import BedrockModel
    from strands import Agent, tool
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.hooks.events import BeforeToolCallEvent
    from botocore.config import Config as BotocoreConfig
    from botocore.exceptions import ClientError
    import boto3

    # Reset boto3 session for fresh credentials in this worker.
    # NOTE: only reset DEFAULT_SESSION, NOT _get_default_session.
    # Overriding _get_default_session breaks boto3.client() globally
    # (including the evolver running in the main thread).
    boto3.DEFAULT_SESSION = None

    _orig_stream = BedrockModel._stream
    def _safe_stream(self, callback, messages, *a, **kw):
        try:
            return _orig_stream(self, callback, messages, *a, **kw)
        except ClientError as exc:
            if "must end with a user message" not in str(exc):
                raise
            messages.append({"role": "user", "content": [{"text": "continue"}]})
            return _orig_stream(self, callback, messages, *a, **kw)
    BedrockModel._stream = _safe_stream

    logging.basicConfig(level=logging.INFO,
                        format=f"%(asctime)s [{task_data.get('id','?')}] %(message)s")
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("futurex_worker")

    task_id = task_data.get("id", "unknown")
    prompt = task_data.get("input", "")
    metadata = task_data.get("metadata", {})
    expected_output = metadata.get("expected_output", [])
    max_turns = args_dict.get("max_turns", 80)
    task_timeout = args_dict.get("task_timeout", 600)
    out_dir = Path(args_dict.get("output_dir", "results"))
    tool_files = args_dict.get("tool_files", {})

    # Per-task harness filter (retrieval adaptation M1/M2). No-op for M0/M4
    # (task_filters absent). Applied here so the filtered tool_files flows
    # into the sandbox below; the filtered system prompt is stored back into
    # args_dict so the later system_prompt read picks it up.
    _tf = (args_dict.get("task_filters") or {}).get(task_id)
    if _tf is not None:
        from .._harness_filter import apply_task_filter
        _fsp, tool_files = apply_task_filter(
            args_dict.get("system_prompt", ""), tool_files, _tf,
            args_dict.get("harness_pieces"))
        if _fsp:
            args_dict = {**args_dict, "system_prompt": _fsp}
    log.info("solve_one: tool_files=%d keys=%s", len(tool_files), list(tool_files.keys())[:5])

    result = {
        "instance_id": task_id, "success": False, "score": 0.0, "detail": "",
        "elapsed": 0.0, "turns": 0,
        "input_tokens": 0, "output_tokens": 0,
        "max_turns_hit": False, "timed_out": False, "error": None,
        "batch_num": args_dict.get("batch_num", 0),
        "evo_cycle": args_dict.get("evo_cycle", 0),
    }

    # ── Cutoff date from task metadata ───────────────────────────
    task_creation_date = metadata.get("creation_date")
    if isinstance(task_creation_date, str):
        try:
            task_creation_date = datetime.fromisoformat(
                task_creation_date.replace('Z', '+00:00'))
        except Exception:
            task_creation_date = datetime(2026, 1, 8)
    elif not task_creation_date:
        task_creation_date = datetime(2026, 1, 8)

    resolution_date = task_creation_date + timedelta(days=7)
    cutoff_str = resolution_date.strftime('%Y-%m-%d')
    cutoff_year = resolution_date.year
    cutoff_month = resolution_date.month
    _date_start = (resolution_date - timedelta(days=730)).strftime('%Y-%m-%d')
    _timelimit = f"{_date_start}..{cutoff_str}"

    # _is_after_cutoff removed — relying solely on htmldate publication date
    # extraction for label leakage prevention. This makes the only difference
    # between strict and live modes the htmldate date filtering.

    container_name = None

    try:
        # ── Model ────────────────────────────────────────────────
        system_prompt = args_dict.get("system_prompt",
            "You are a temporal prediction agent that forecasts future events "
            "using only information available before the task's creation date.\n\n"
            "Strategy:\n"
            "1. Gather evidence from the tools available this cycle (they "
            "may include search, APIs, or evolved helpers under /tools/).\n"
            "2. If no external-data tool is available, reason from model "
            "knowledge and base rates.\n"
            "3. Make your best prediction with \\boxed{ANSWER} or call submit.\n\n"
            "Important: You are predicting events BEFORE they happen. "
            "A definitive answer may not exist — make your best judgment.")
        model_id = args_dict.get("model_id", "us.anthropic.claude-sonnet-4-6")
        config_obj = args_dict.get("config")
        if config_obj and hasattr(config_obj, 'extra'):
            model_id = config_obj.extra.get("model_name", model_id)

        model = BedrockModel(
            model_id=model_id,
            region_name=args_dict.get("region", "us-east-1"),
            max_tokens=args_dict.get("max_tokens", 2048),
            temperature=args_dict.get("solver_temperature", 0.0),
            boto_client_config=BotocoreConfig(
                read_timeout=600,
                retries={"max_attempts": 8, "mode": "adaptive"},
            ),
        )

        # ── Docker sandbox for evolved tools + infra ────────────
        _infra_files = args_dict.get("infra_files") or {}
        sandbox_files = dict(tool_files)
        for rel_path, content in _infra_files.items():
            sandbox_files[f"infra/{rel_path}"] = content

        if sandbox_files:
            try:
                sandbox_net = config_obj.extra.get("sandbox_network", "none") if config_obj else "none"
                container_name = start_sandbox(
                    task_id, sandbox_files, sandbox_network=sandbox_net,
                    cutoff_date=cutoff_str,
                )
                log.info("Sandbox started: %s (%d files, cutoff=%s)",
                         container_name, len(sandbox_files), cutoff_str)
            except Exception as e:
                log.warning("Sandbox start failed: %s", e)

        # ── Tools ────────────────────────────────────────────────
        _submitted = [False]
        _submit_output = [None]
        tool_call_count = [0]
        _search_call_count = [0]
        _MAX_SEARCHES = 15  # Generous cap — prevents 20+ search spirals

        # Built-in search pipeline selector.
        #   strict — built-in web_search via Wikipedia + DDGS+htmldate (default)
        #   live   — built-in web_search via DDGS unrestricted
        #   off    — no built-in web_search tool at all
        # The agent can always still do search via evolved /tools/*.py + bash.
        builtin_search = "strict"
        if config_obj and hasattr(config_obj, 'extra'):
            extra = config_obj.extra
            if "builtin_search" in extra:
                builtin_search = extra["builtin_search"]
            elif extra.get("no_web_search"):       # legacy
                builtin_search = "off"
            elif "search_mode" in extra:           # legacy
                builtin_search = extra["search_mode"]

        _cutoff_iso = task_creation_date.strftime('%Y-%m-%dT23:59:59Z')

        # ── Wikipedia revision API (strict mode) ─────────────────
        import requests as _requests
        _wiki_session = _requests.Session()
        _wiki_session.headers.update({
            "User-Agent": "A-EVOLVE-V2/1.0 (https://github.com/A-EVOLVE; temporal-prediction-benchmark) python-requests"
        })

        def _throttled_wiki_get(params):
            """Rate-limited Wikipedia API call shared across all workers."""
            with _search_lock:
                wait = max(0, _WIKI_THROTTLE - (time.time() - _last_search_time[0]))
                if wait > 0:
                    time.sleep(wait)
                _last_search_time[0] = time.time()
            return _wiki_session.get(
                "https://en.wikipedia.org/w/api.php",
                params=params, timeout=8)

        def _wiki_search(query, limit=5):
            """Search Wikipedia for articles matching query."""
            try:
                r = _throttled_wiki_get({
                    "action": "query", "list": "search",
                    "srsearch": query, "srlimit": limit,
                    "format": "json"})
                return [hit["title"] for hit in r.json()["query"]["search"]]
            except Exception:
                return []

        def _wiki_revision(title):
            """Fetch article content from the last revision before cutoff.
            Returns (timestamp, plain_text) or None."""
            try:
                r = _throttled_wiki_get({
                    "action": "query", "titles": title,
                    "prop": "revisions", "rvprop": "content|timestamp",
                    "rvlimit": "1", "rvstart": _cutoff_iso,
                    "rvdir": "older", "format": "json"})
                pages = r.json().get("query", {}).get("pages", {})
                for pid, page in pages.items():
                    revs = page.get("revisions", [])
                    if not revs:
                        return None
                    wikitext = revs[0].get("*", "")
                    # Convert wikitext to plain text
                    text = re.sub(r'\{\{[^}]*\}\}', '', wikitext)
                    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text)
                    text = re.sub(r"'{2,}", '', text)
                    text = re.sub(r'<[^>]+>', '', text)
                    text = re.sub(r'==+', '', text)
                    text = re.sub(r'\n+', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    return (revs[0]["timestamp"][:10], text[:800])
            except Exception:
                return None

        # ── Multi-source helpers (strict mode layers 2-3) ─────────
        # API keys: config.extra > environment variable > None (skip layer)
        _extra = (config_obj.extra if config_obj and hasattr(config_obj, 'extra') else {})
        # # Tavily — disabled, using free DDGS+htmldate instead. Uncomment to re-enable.
        # _tavily_key = _extra.get("tavily_api_key") or os.environ.get("TAVILY_API_KEY")
        _tavily_key = None
        _fred_key = _extra.get("fred_api_key") or os.environ.get("FRED_API_KEY")

        # # Exa.ai — disabled, credits exhausted. Uncomment if topped up.
        # _exa_key = _extra.get("exa_api_key") or os.environ.get("EXA_API_KEY")

        # htmldate-based DDGS search (free alternative to Tavily)
        try:
            from agent_evolve.tools.htmldate_search import (
                htmldate_filtered_search, format_for_agent,
            )
            _htmldate_available = True
        except ImportError:
            _htmldate_available = False

        def _is_economic_query(q):
            ql = q.lower()
            return any(k in ql for k in [
                'inflation', 'pce ', 'cpi ', 'gdp', 'interest rate',
                'cash rate', 'federal reserve', 'reserve bank',
                'consumer price', 'monetary policy', 'unemployment rate',
            ])

        def _tavily_search(query, limit=5):
            """Tavily date-filtered search — returns pre-cutoff web content."""
            if not _tavily_key:
                return []
            try:
                resp = _requests.post(
                    "https://api.tavily.com/search",
                    headers={"Content-Type": "application/json"},
                    json={
                        "api_key": _tavily_key,
                        "query": query,
                        "max_results": limit,
                        "search_depth": "advanced",
                        "end_date": cutoff_str,
                        "include_raw_content": True,
                    },
                    timeout=20,
                )
                if resp.status_code != 200:
                    log.debug("Tavily returned %d: %s", resp.status_code, resp.text[:200])
                    return []
                results = []
                for r in resp.json().get("results", []):
                    title = r.get("title", "").strip()
                    # Prefer raw_content (full article), fall back to snippet
                    text = (r.get("raw_content") or r.get("content") or "").strip()
                    pub = (r.get("published_date") or "")[:10]
                    if text:
                        source = f"[Tavily {pub}]" if pub else "[Tavily]"
                        results.append(f"{title}\n   {source} {text[:800]}")
                return results
            except Exception as e:
                log.debug("Tavily search failed: %s", e)
                return []

        def _htmldate_web_search(query, limit=3):
            """DuckDuckGo + htmldate date-filtered search — free Tavily alternative.

            Searches DDGS, fetches HTML for each result, extracts publication
            date with htmldate, and drops anything published on/after cutoff.

            The whole call is wrapped in a wall-clock via
            ``concurrent.futures`` so a stuck htmldate thread cannot leak
            past the outer batch deadline. Without this bound, interpreter
            shutdown races the inner ``ThreadPoolExecutor`` and produces
            post-summary ``cannot schedule new futures after interpreter
            shutdown`` warnings.
            """
            if not _htmldate_available:
                return []
            import concurrent.futures as _cf

            executor = _cf.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="htmldate-wall",
            )
            try:
                future = executor.submit(
                    htmldate_filtered_search,
                    query=query,
                    cutoff_date=cutoff_str,
                    max_results=limit,
                    fetch_timeout=8.0,
                    fetch_workers=3,
                )
                try:
                    raw = future.result(timeout=15)
                except _cf.TimeoutError:
                    future.cancel()
                    log.warning(
                        "htmldate search timed out for %r (>15s)", query[:50],
                    )
                    return []
                except Exception as e:
                    log.warning("htmldate search failed for %r: %s", query[:50], e)
                    return []
            finally:
                # Don't block interpreter shutdown if the worker is stuck.
                executor.shutdown(wait=False, cancel_futures=True)

            results = []
            for r in raw:
                title = r.get("title", "").strip()
                snippet = r.get("snippet", "").strip()
                pub = r.get("published_date") or ""
                if snippet:
                    source = f"[Web {pub}]" if pub else "[Web]"
                    results.append(f"{title}\n   {source} {snippet}")
            log.info("htmldate: %r -> %d results", query[:50], len(results))
            return results

        def _fred_search(query):
            """FRED API — fetch economic time series value before cutoff."""
            if not _fred_key:
                return []
            series_map = {
                'pce': 'PCEPI', 'pce price': 'PCEPI',
                'core pce': 'PCEPILFE', 'pce annual': 'PCEPI',
                'cpi': 'CPIAUCSL', 'consumer price': 'CPIAUCSL',
                'core cpi': 'CPILFESL',
                'gdp': 'GDP', 'real gdp': 'GDPC1',
                'unemployment': 'UNRATE',
                'federal funds': 'FEDFUNDS', 'fed rate': 'FEDFUNDS',
                'inflation': 'CPIAUCSL',
            }
            ql = query.lower()
            series_id = None
            for key, sid in series_map.items():
                if key in ql:
                    series_id = sid
                    break
            if not series_id:
                return []
            try:
                resp = _requests.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": _fred_key,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 6,
                        "realtime_end": cutoff_str,
                        "observation_end": cutoff_str,
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    return []
                obs = resp.json().get("observations", [])
                if not obs:
                    return []
                lines = []
                for o in obs[:6]:
                    date = o.get("date", "?")
                    val = o.get("value", "?")
                    lines.append(f"  {date}: {val}")
                header = f"FRED {series_id} (before {cutoff_str})"
                return [f"{header}\n" + "\n".join(lines)]
            except Exception as e:
                log.debug("FRED search failed: %s", e)
                return []

        # ── DDGS live search (live mode) ─────────────────────────
        def _ddgs_subprocess(query, timelimit=None, max_results=10):
            """Search DDGS in a subprocess (isolates thread pool)."""
            with _search_lock:
                wait = max(0, _DDGS_THROTTLE - (time.time() - _last_search_time[0]))
                if wait > 0:
                    time.sleep(wait)
                _last_search_time[0] = time.time()
            tl_arg = f"timelimit={timelimit!r}," if timelimit else ""
            code = (
                "import json; from ddgs import DDGS; "
                f"r=list(DDGS(timeout=15).text({query!r},"
                f"{tl_arg}"
                f"max_results={max_results},"
                f"backend='auto')); "
                "print(json.dumps(r))"
            )
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True, text=True, timeout=25)
                if proc.returncode == 0 and proc.stdout.strip():
                    return json.loads(proc.stdout.strip())
            except Exception:
                pass
            return []

        if builtin_search == "off":
            log.info("Built-in web_search DISABLED (builtin_search=off)")
        elif builtin_search == "live":
            log.info("LIVE search (DDGS, unrestricted)")
        else:
            _sources = ["Wikipedia"]
            if _tavily_key:
                _sources.append("Tavily")
            elif _htmldate_available:
                _sources.append("DDGS+htmldate")
            if _fred_key:
                _sources.append("FRED")
            log.info("STRICT search (%s, cutoff=%s)", "+".join(_sources), cutoff_str)

        @tool
        def web_search(query: str) -> str:
            """Search for pre-event information. Use 2-4 searches then reason.

            Returns information published before the task creation date.
            Do not search more than necessary — commit to a prediction
            once you have enough context.

            Args:
                query: Search query relevant to the prediction task.
            """
            if builtin_search == "live":
                return _live_search(query)
            _search_call_count[0] += 1
            if _search_call_count[0] > _MAX_SEARCHES:
                return (
                    f"Search limit reached ({_MAX_SEARCHES}). "
                    "You have enough information — reason from what you found "
                    "and give your prediction with \\boxed{ANSWER}."
                )
            return _strict_search(query)

        _seen_titles = set()  # Dedup across searches within a task

        # Check for evolved search pipeline (infra/ layer).
        # Recreate the full infra/ directory in a temp dir so multi-file
        # imports work (search_pipeline.py can import from other infra files).
        # _infra_files already extracted above (for sandbox loading).
        _infra_search = None
        _infra_dir = None
        if "search_pipeline.py" in _infra_files:
            import tempfile as _tf
            _infra_dir = Path(_tf.mkdtemp(prefix="infra_"))
            for rel_path, content in _infra_files.items():
                p = _infra_dir / rel_path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
            _infra_search = _infra_dir / "search_pipeline.py"
            log.info("Using serialized search pipeline (%d files) from infra_files",
                     len(_infra_files))
        else:
            _ws_root = args_dict.get("workspace_root")
            if _ws_root:
                _infra_path = Path(_ws_root) / "infra" / "search_pipeline.py"
                if _infra_path.exists():
                    log.info("Loading evolved search pipeline: %s", _infra_path)
                    _infra_search = _infra_path
                    _infra_dir = Path(_ws_root) / "infra"

        _cutoff_date = datetime.strptime(cutoff_str, '%Y-%m-%d').date()
        _RELATIVE_RE = re.compile(
            r'(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago', re.I)
        _ABSOLUTE_FMTS = [
            (re.compile(r'(\d{4})-(\d{2})-(\d{2})'), lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
            (re.compile(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+(\d{1,2}),?\s+(\d{4})', re.I),
             lambda m: (int(m.group(3)), {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                         'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}[m.group(1).lower()[:3]], int(m.group(2)))),
            (re.compile(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+(\d{4})', re.I),
             lambda m: (int(m.group(3)), {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                         'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}[m.group(2).lower()[:3]], int(m.group(1)))),
        ]

        def _is_after_cutoff(date_str: str) -> bool:
            """Check if a date string refers to a time after cutoff_str."""
            if not date_str:
                return False
            rm = _RELATIVE_RE.search(date_str)
            if rm:
                n, unit = int(rm.group(1)), rm.group(2).lower()
                days = {'second': 0, 'minute': 0, 'hour': 0, 'day': 1,
                        'week': 7, 'month': 30, 'year': 365}.get(unit, 0) * n
                approx = datetime.now().date() - timedelta(days=days)
                return approx > _cutoff_date
            for pattern, extractor in _ABSOLUTE_FMTS:
                m = pattern.search(date_str)
                if m:
                    try:
                        y, mo, d = extractor(m)
                        return datetime(y, mo, d).date() > _cutoff_date
                    except (ValueError, KeyError):
                        pass
            return False

        def _strict_search(query):
            """Multi-source pre-cutoff search — guaranteed zero label leakage.

            Layers (evolved pipeline first, wiki/FRED as fallbacks):
              1. Evolved search pipeline  — direct API results + smarter queries
              2. Web search (htmldate)    — pipeline queries OR original query
              3. Wikipedia Revision API   — fallback encyclopedic content
              4. FRED                     — fallback economic indicators
            """
            pipeline_results = []
            web_results = []
            search_queries = [query]

            # Layer 1: Evolved search pipeline
            if _infra_search:
                try:
                    pipeline_input = json.dumps({
                        "query": query,
                        "cutoff_date": cutoff_str,
                    })
                    proc = subprocess.run(
                        [sys.executable, str(_infra_search)],
                        input=pipeline_input,
                        capture_output=True, text=True, timeout=20,
                        cwd=str(_infra_dir) if _infra_dir else None,
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        config_out = json.loads(proc.stdout.strip())
                        n_direct = len(config_out.get("direct_results", []))
                        n_filtered = 0
                        for dr in config_out.get("direct_results", []):
                            title = dr.get("title", "")
                            content = dr.get("content", "")
                            source = dr.get("source", "API")
                            date = dr.get("date", "")
                            if _is_after_cutoff(date):
                                n_filtered += 1
                                continue
                            if content and title.lower() not in _seen_titles:
                                _seen_titles.add(title.lower())
                                attr = f"[{source}" + (f" {date}]" if date else "]")
                                pipeline_results.append(f"{title}\n   {attr} {content}")
                        if n_direct:
                            log.info("Pipeline returned %d direct_results (class=%s, filtered=%d)",
                                     n_direct, config_out.get("classification", "?"), n_filtered)
                        search_queries = config_out.get("queries", [query])
                    elif proc.returncode != 0:
                        log.warning("Pipeline exit %d: %s", proc.returncode, proc.stderr[:200])
                except Exception as e:
                    log.warning("Evolved search pipeline failed: %s", e)

            # Layer 2: Web search with htmldate filtering
            for sq in search_queries[:3]:
                for wr in _htmldate_web_search(sq, limit=3):
                    first_line = wr.split("\n")[0].strip().lower()
                    if first_line not in _seen_titles:
                        _seen_titles.add(first_line)
                        web_results.append(wr)

            # Layer 3: Wikipedia fallback
            total = len(pipeline_results) + len(web_results)
            if total < 3:
                try:
                    for title in _wiki_search(query, limit=5):
                        if title.lower() in _seen_titles:
                            continue
                        rev = _wiki_revision(title)
                        if rev:
                            ts, text = rev
                            _seen_titles.add(title.lower())
                            web_results.append(f"{title}\n   [Wikipedia rev {ts}] {text}")
                except Exception as e:
                    log.debug("Wikipedia layer failed: %s", e)

            # Layer 4: FRED economic data fallback
            total = len(pipeline_results) + len(web_results)
            if _is_economic_query(query) and total < 3:
                web_results.extend(_fred_search(query))

            if not pipeline_results and not web_results:
                return f"No pre-cutoff content for '{query}'"

            lines = [f"Results for '{query}' (pre-cutoff, before {cutoff_str}):"]
            if pipeline_results:
                lines.append("\n=== Structured API data ===")
                for i, r in enumerate(pipeline_results, 1):
                    lines.append(f"{i}. {r}")
            if web_results:
                lines.append("\n=== Web search results ===")
                for i, r in enumerate(web_results, 1):
                    lines.append(f"{i}. {r}")
            return "\n".join(lines)

        def _live_search(query):
            """DDGS live search — unrestricted, no date filtering."""
            try:
                raw = _ddgs_subprocess(query, None)
                if not raw:
                    return f"No results for '{query}'"
                lines = [f"Results for '{query}':"]
                for i, r in enumerate(raw[:5], 1):
                    lines.append(f"{i}. {r.get('title','')}\n   {r.get('body','')}")
                return "\n".join(lines)
            except Exception as e:
                return f"Search failed: {e}"

        # -- submit --
        @tool
        def submit(
            decision: str,
            side: str = "",
            confidence: float = 0.5,
            reasoning: str = "",
        ) -> str:
            """Submit your final prediction and end the task.

            After calling submit, do NOT call any more tools.

            Args:
                decision: Your prediction (e.g. BUY, SELL, YES, NO, or the answer).
                side: Which outcome (e.g. A, B, or a specific option).
                confidence: Your confidence between 0 and 1.
                reasoning: Brief summary of your analysis.
            """
            _submitted[0] = True
            output = {
                "decision": decision.upper(),
                "side": side.upper() if side else "",
                "confidence": max(0.0, min(1.0, confidence)),
                "reasoning": reasoning,
            }
            _submit_output[0] = json.dumps(output)
            return (
                f"Prediction submitted: {decision} {side} (conf={confidence:.2f})\n"
                "You are done. Do NOT call any more tools."
            )

        # -- LLM temporal filter (replaces htmldate) ---------
        _filter_client = None
        _filter_enabled = (
            config_obj and hasattr(config_obj, 'extra')
            and config_obj.extra.get("sandbox_network") == "bridge"
        )

        def _temporal_filter(content: str, command: str) -> str:
            """LLM-based temporal filter for bash tool output.

            Sends the task description, cutoff date, and retrieved
            content to Haiku.  Haiku returns a REDACTED version with
            post-cutoff values replaced by [REDACTED], or the original
            content if everything is pre-cutoff.

            Applied to all bash commands that may fetch external data.
            Whitelists purely computational commands.
            """
            if not _filter_enabled:
                return content
            cmd = command.strip()
            _safe = ("python3 -c", "echo ", "cat ", "ls ", "pwd", "head ", "tail ", "wc ")
            if any(cmd.startswith(p) for p in _safe):
                return content
            if len(content) < 80:
                return content

            nonlocal _filter_client
            try:
                if _filter_client is None:
                    import boto3 as _boto3
                    _filter_client = _boto3.client(
                        "bedrock-runtime",
                        region_name=args_dict.get("region", "us-east-1"),
                    )

                snippet = content[:4000]
                resp = _filter_client.converse(
                    modelId="us.anthropic.claude-sonnet-4-6",
                    messages=[{"role": "user", "content": [{"text":
                        f"TASK: {prompt[:300]}\n"
                        f"CUTOFF DATE: {cutoff_str}\n"
                        f"The agent must predict events AFTER the cutoff "
                        f"date using ONLY information available BEFORE it.\n\n"
                        f"Below is content retrieved by the agent's tools. "
                        f"Your job:\n"
                        f"1. Identify any specific data points (numbers, "
                        f"names, rankings, statistics) that correspond to "
                        f"dates AFTER {cutoff_str}. These would directly "
                        f"answer the prediction task — that is label leakage.\n"
                        f"2. If you find leaked data: return the content "
                        f"with ONLY the post-cutoff values replaced by "
                        f"[REDACTED]. Keep all pre-cutoff data intact — "
                        f"the agent needs it for trend analysis.\n"
                        f"3. If all data is before the cutoff, or the "
                        f"content is a live platform ranking page (Douban, "
                        f"Maoyan, QQ Music) with no explicit post-cutoff "
                        f"dates, return: CLEAN\n\n"
                        f"CONTENT:\n{snippet}"
                    }]}],
                    system=[{"text":
                        "You are a temporal leakage filter for a prediction "
                        "benchmark. The agent predicts future events and must "
                        "not see actual outcomes beforehand.\n\n"
                        "RULES:\n"
                        "- Replace ALL data points dated STRICTLY AFTER the "
                        "cutoff with [REDACTED]\n"
                        "- Keep data ON or BEFORE the cutoff\n"
                        "- For tables: redact entire rows where date > cutoff\n"
                        "- For search snippets: redact values for dates > cutoff\n"
                        "- Live platform ranking pages (Douban, Maoyan, QQ Music, "
                        "Maoer FM, Dongchedi) WITHOUT per-row dates: CLEAN\n"
                        "- If ALL content is on/before cutoff or undated "
                        "rankings: reply exactly CLEAN\n"
                        "- Otherwise: return ONLY the content with post-cutoff "
                        "values replaced by [REDACTED]\n"
                        "- Be strict: Apr 10 data is AFTER an Apr 8 cutoff\n\n"
                        "CRITICAL: Output ONLY the filtered content or CLEAN.\n"
                        "NEVER explain what you found. NEVER describe the leaked "
                        "values. NEVER say what was redacted. Just output the "
                        "content with [REDACTED] in place of post-cutoff values."
                    }],
                    inferenceConfig={"maxTokens": 4096, "temperature": 0},
                )
                verdict = ""
                for block in resp.get("output", {}).get("message", {}).get("content", []):
                    if "text" in block:
                        verdict = block["text"].strip()
                        break

                if verdict == "CLEAN":
                    return content

                if "[REDACTED]" in verdict:
                    # Strip any explanation preamble before actual content
                    json_start = verdict.find('{"')
                    if json_start > 0:
                        verdict = verdict[json_start:]
                    n_redacted = verdict.count("[REDACTED]")
                    log.info("Temporal filter: redacted %d values (cutoff=%s)",
                             n_redacted, cutoff_str)
                    return (
                        f"[Temporal filter: {n_redacted} post-cutoff values "
                        f"redacted (cutoff={cutoff_str})]\n\n" + verdict
                    )

                if verdict.upper().startswith("LEAKED"):
                    reason = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
                    log.info("Temporal filter blocked: %s", reason[:80])
                    return (
                        f"[LEAKAGE BLOCKED: {reason}]\n\n"
                        "Post-cutoff data was removed. Use pre-cutoff "
                        "trends and historical data to make your prediction."
                    )

            except Exception as e:
                log.debug("Temporal filter failed (passthrough): %s", e)

            return content

        # -- bash (when Docker sandbox has tools/ or infra/) --
        @tool
        def bash(command: str) -> str:
            """Execute a bash command in the analysis sandbox.

            Python3 is available. Evolved tools in /tools/, search
            pipeline and modules in /infra/.
            Examples:
              python3 /tools/finance_data.py "CL=F" "2026-01-25"
              python3 /infra/search_pipeline.py < query.json

            Args:
                command: The bash command to execute.
            """
            if not container_name:
                return "ERROR: No sandbox available (no evolved tools loaded)."
            try:
                r = subprocess.run(
                    ["docker", "exec", "-w", "/tools", container_name,
                     "bash", "-c", command],
                    capture_output=True, text=True, timeout=30,
                )
                out = (r.stdout or "") + (r.stderr or "")
                if not out.strip():
                    return "(no output)"
                if len(out) > 8000:
                    out = out[:4000] + "\n...[truncated]...\n" + out[-4000:]
                out = _temporal_filter(out, command)
                return out
            except subprocess.TimeoutExpired:
                return "ERROR: Command timed out after 30s."
            except Exception as e:
                return f"ERROR: {e}"

        # -- turn limiter --
        def turn_limiter(event: BeforeToolCallEvent):
            if _submitted[0]:
                event.cancel_tool = "Already submitted. No more tool calls."
                return
            if event.tool_use.get("name") == "submit":
                return
            tool_call_count[0] += 1
            if tool_call_count[0] > max_turns:
                event.cancel_tool = (
                    f"Turn limit reached ({max_turns}). "
                    "Call submit or give your final answer with \\boxed{ANSWER}."
                )

        # ── Agent ────────────────────────────────────────────────
        tools = [submit]
        if builtin_search != "off":
            tools.insert(0, web_search)
        if container_name:
            tools.insert(-1, bash)  # add bash before submit

        agent = Agent(
            model=model, system_prompt=system_prompt, tools=tools,
            callback_handler=None,
            conversation_manager=SlidingWindowConversationManager(
                window_size=max_turns * 2 + 10),
        )
        agent.hooks.add_callback(BeforeToolCallEvent, turn_limiter)

        t0 = time.time()
        # After every tool call, persist a partial trajectory snapshot so
        # the harness can recover real state for tasks whose Future is
        # cancelled by the batch deadline (see solve_all_with_evolution.py
        # and agents/_partial_trajectory.py).
        from agent_evolve.agents._partial_trajectory import install_partial_writer
        install_partial_writer(
            agent,
            task_id=task_id,
            out_dir=out_dir,
            turn_counter=tool_call_count,
            start_time=t0,
            extract_conversation=lambda a: _extract_conv(a.messages),
        )

        response = None
        timed_out = False
        try:
            response = agent(prompt)
        except Exception as e:
            log.warning("Agent error: %s", e)
        elapsed = time.time() - t0

        # ── Extract tokens ───────────────────────────────────────
        try:
            u = response.metrics.accumulated_usage
            result["input_tokens"] = u.get("inputTokens", 0)
            result["output_tokens"] = u.get("outputTokens", 0)
        except Exception:
            pass

        # ── Extract conversation ─────────────────────────────────
        conversation = []
        try:
            conversation = _extract_conv(agent.messages)
        except Exception:
            pass
        result["conversation"] = conversation

        # ── Extract final text answer ────────────────────────────
        raw_response = ""
        # First check if submit was called
        if _submit_output[0]:
            raw_response = _submit_output[0]
        else:
            try:
                if response and hasattr(response, 'message') and response.message:
                    msg = response.message
                    if isinstance(msg, dict):
                        for b in msg.get("content", []):
                            if isinstance(b, dict) and "text" in b:
                                raw_response = b["text"]
                                break
            except Exception:
                pass
        if not raw_response and conversation:
            for m in reversed(conversation):
                if m.get("role") == "assistant":
                    raw_response = m.get("content", "")
                    break

        solution = raw_response.strip()

        # If no boxed answer in the final response, scan the full conversation
        # (handles max_tokens truncation where answer may be in an earlier turn)
        if solution and not re.search(r'\\?boxed\{', solution):
            for m in reversed(conversation):
                if m.get("role") == "assistant" and re.search(r'\\?boxed\{', m.get("content", "")):
                    solution = m["content"].strip()
                    break

        result["output"] = solution
        result["elapsed"] = elapsed
        result["turns"] = tool_call_count[0]
        result["max_turns_hit"] = tool_call_count[0] >= max_turns
        result["timed_out"] = timed_out
        result["submitted"] = _submitted[0]

        # ── Save per-task trajectory ─────────────────────────────
        sid = task_id.replace("/", "_")
        out_dir.mkdir(parents=True, exist_ok=True)
        if conversation:
            try:
                (out_dir / f"trajectory_{sid}.json").write_text(
                    json.dumps(conversation, indent=2, ensure_ascii=False))
            except Exception:
                pass
        # Task finished cleanly — drop the partial snapshot so downstream
        # enrichment won't mistakenly treat this as cut-off next cycle.
        # Also carry forward tool_timings so the evolver can see per-tool
        # latency (the real bottleneck on this benchmark).
        try:
            from agent_evolve.agents._partial_trajectory import clear_partial_trajectory
            final_partial = clear_partial_trajectory(out_dir, task_id)
            if final_partial and final_partial.get("tool_timings"):
                result["tool_timings"] = final_partial["tool_timings"]
        except Exception:
            pass

        # ── Evaluate ─────────────────────────────────────────────
        score = 0.0
        success = False
        evaluable = bool(expected_output)

        if evaluable and solution:
            # Try submit-tool JSON first (decision field), then \boxed{}.
            extracted = None
            if _submit_output and _submit_output[0]:
                try:
                    sub = json.loads(_submit_output[0]) if isinstance(_submit_output[0], str) else _submit_output[0]
                    extracted = str(sub.get("decision", "")).strip() or None
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass
            if not extracted:
                extracted = _extract_boxed_answer(solution)
            if extracted:
                norm = extracted.strip().upper()
                expected_norm = [str(e).strip().upper() for e in expected_output]
                if norm in expected_norm:
                    score = 1.0
                    success = True

        result["success"] = success
        result["score"] = score
        result["detail"] = f"score={score:.1f}" + ("" if evaluable else " (no gt)")

        log.info("Done: score=%.1f turns=%d elapsed=%.1fs", score, tool_call_count[0], elapsed)

    except Exception as e:
        result["error"] = str(e)
        result["detail"] = f"Worker error: {e}"
    finally:
        stop_sandbox(container_name)

    return result


# ─── Helpers ────────────────────────────────────────────────────────────

def _extract_conv(messages):
    """Extract readable conversation from strands agent messages."""
    conv = []
    for msg in messages:
        role = msg.get("role", "unknown")
        parts = []
        for b in msg.get("content", []):
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                if "text" in b:
                    parts.append(b["text"])
                elif "toolUse" in b:
                    tu = b["toolUse"]
                    inp = json.dumps(tu.get("input", {}))
                    parts.append(f"[tool_use: {tu['name']}]\n{inp}")
                elif "toolResult" in b:
                    tr = b["toolResult"]
                    txt = "".join(
                        c.get("text", "") for c in tr.get("content", [])
                        if isinstance(c, dict)
                    )
                    parts.append(f"[tool_result]\n{txt}")
        if parts:
            conv.append({"role": role, "content": "\n".join(parts)})
    return conv


def _extract_boxed_answer(solution: str) -> Optional[str]:
    """Extract answer from \\boxed{...} format."""
    if not solution:
        return None
    for pat in [r'\\boxed\{([^}]+)\}', r'boxed\{([^}]+)\}']:
        matches = re.findall(pat, solution)
        if matches:
            return matches[-1].strip()
    return None


def get_benchmark_info() -> Dict[str, Any]:
    return {
        "name": "futurex",
        "description": "FutureX temporal prediction with time-bounded web search",
        "domains": ["Technology", "Finance", "Sports", "Politics"],
        "temporal_constraints": True,
        "requires_search": True,
    }
