from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent.parent
DEFAULT_SESSIONS_DIR = (
    PROJECT_ROOT / "agentic-evolution" / "tools_sandbox" / "sessions"
)
DEFAULT_EXPERIMENTS_DIR = (
    PROJECT_ROOT / "agentic-evolution" / "experiments" / "outputs"
)


def _detect_python_bin() -> str:
    """Find the best Python binary for running evolve scripts.

    Priority:
    1. AEVOLVE_PYTHON_BIN env var (explicit override)
    2. 'aevolve' conda env Python (has appworld + anthropic)
    3. Current interpreter (fallback)
    """
    explicit = os.getenv("AEVOLVE_PYTHON_BIN")
    if explicit:
        return explicit
    # Try common conda paths for the 'aevolve' env
    for base in [
        Path.home() / "miniconda3",
        Path.home() / "anaconda3",
        Path("/opt/homebrew/Caskroom/miniconda/base"),
        Path("/opt/homebrew/Caskroom/miniforge/base"),
    ]:
        candidate = base / "envs" / "aevolve" / "bin" / "python3"
        if candidate.exists():
            return str(candidate)
    return sys.executable


EVOLVE_PYTHON_BIN = _detect_python_bin()

SESSIONS_DIR = Path(os.getenv("AEVOLVE_SESSIONS_DIR", str(DEFAULT_SESSIONS_DIR)))
EXPERIMENTS_DIR = Path(
    os.getenv("AEVOLVE_EXPERIMENTS_DIR", str(DEFAULT_EXPERIMENTS_DIR))
)

DEFAULT_APPWORLD_EXAMPLES_DIR = PROJECT_ROOT / "agentic-evolution" / "examples"

MODEL_OPTIONS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-5-20250929",
    "gpt-5",
    "gpt-5-mini",
    "gemini-3-flash-preview",
]

_STAGE_RE = re.compile(r"STAGE\s+([1-4])", re.IGNORECASE)
_DIAGNOSIS_RE = re.compile(r"\[Proposer\].*STAGE\s*1\s*:\s*DIAGNOSIS", re.IGNORECASE)
_PLAN_RE = re.compile(r"\[Proposer\].*STAGE\s*2\s*:\s*PLAN", re.IGNORECASE)
_APPLY_RE = re.compile(r"\[Proposer\].*STAGE\s*3\s*:\s*APPLY", re.IGNORECASE)
_VERIFY_RE = re.compile(r"\[Proposer\].*STAGE\s*4\s*:\s*VERIFY", re.IGNORECASE)
_PROPOSAL_ACTION_RE = re.compile(r"Proposal action:\s*(.+)$", re.IGNORECASE)
_VERIFIED_RE = re.compile(
    r"(Verified and committed|Changes reverted due to verification failure)",
    re.IGNORECASE,
)
_BATCH_PROGRESS_RE = re.compile(r"Batch\s+(\d+)/(\d+)", re.IGNORECASE)
_TASK_START_RE = re.compile(r"Task\s+(\d+)/(\d+):\s*(\S+)")
_STEP_PROGRESS_RE = re.compile(r"Step\s+(\d+)/(\d+)")
_TASK_DONE_RE = re.compile(r"✓\s*Task\s+(\S+):\s*score=([\d.]+),\s*steps=(\d+)")
_PASSED_TESTS_RE = re.compile(r"Num Passed Tests\s*:\s*(\d+)", re.IGNORECASE)
_FAILED_TESTS_RE = re.compile(r"Num Failed Tests\s*:\s*(\d+)", re.IGNORECASE)
_TOTAL_TESTS_RE = re.compile(r"Num Total\s+Tests\s*:\s*(\d+)", re.IGNORECASE)


class EvolveStartRequest(BaseModel):
    environment: str = Field(default="appworld")
    main_model: str = Field(default="claude-haiku-4-5-20251001")
    proposer_model: str = Field(default="claude-sonnet-4-5-20250929")
    num_tasks: int = Field(default=10, ge=1, le=500)
    batch_size: int = Field(default=5, ge=1, le=100)
    num_test_tasks: int = Field(default=10, ge=1, le=500)
    on_policy: bool = True
    eval_vanilla: bool = True
    eval_evolved: bool = True
    api_key: str | None = Field(default=None, description="API key provided from the UI")


@dataclass
class EvolveRun:
    session_id: str
    environment: str
    main_model: str
    proposer_model: str
    process: subprocess.Popen[str]
    command: list[str]
    started_at: float
    log_path: Path
    status: str = "running"
    return_code: int | None = None
    error: str | None = None


RUNS: dict[str, EvolveRun] = {}


def _running_run() -> EvolveRun | None:
    for run in RUNS.values():
        if run.process.poll() is None and run.status == "running":
            return run
    return None


def _refresh_run_status(run: EvolveRun) -> None:
    result = run.process.poll()
    if result is None:
        run.status = "running"
        return
    run.return_code = result
    if run.status == "running":
        run.status = "completed" if result == 0 else "failed"
        if result != 0 and run.error is None:
            run.error = f"process_exit_code_{result}"


def _session_id_from_model(model_name: str, train_samples: int) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cleaned = model_name.replace("/", "_").replace(":", "_").replace(".", "_")
    if "claude" in cleaned:
        model_part = "_".join(
            [
                p
                for p in cleaned.split("_")
                if any(k in p.lower() for k in ["claude", "sonnet", "haiku", "opus"])
                or p.isdigit()
            ]
        )
    elif "gpt" in cleaned or cleaned.startswith("o"):
        model_part = "_".join(
            [p for p in cleaned.split("_") if "gpt" in p.lower() or p.isdigit()]
        )
    else:
        model_part = cleaned[:30]
    model_part = model_part or "model"
    return f"appworld_{model_part}_train_{train_samples}_{timestamp}"


def _script_for_model(main_model: str) -> str:
    lowered = main_model.lower()
    if lowered.startswith("gpt") or lowered.startswith("gemini"):
        return "run_appworld_gpt.py"
    return "run_appworld_claude.py"


def _required_api_env(main_model: str) -> str:
    lowered = main_model.lower()
    if lowered.startswith("gpt") or lowered.startswith("o"):
        return "OPENAI_API_KEY"
    if lowered.startswith("gemini"):
        return "GOOGLE_API_KEY"
    return "ANTHROPIC_API_KEY"


def _build_start_command(payload: EvolveStartRequest, session_id: str) -> tuple[list[str], Path]:
    examples_dir = Path(
        os.getenv("AEVOLVE_EXAMPLES_DIR", str(DEFAULT_APPWORLD_EXAMPLES_DIR))
    )
    script_name = _script_for_model(payload.main_model)
    script_path = examples_dir / script_name
    if not script_path.exists():
        raise HTTPException(status_code=500, detail=f"Missing script: {script_path}")

    command = [
        EVOLVE_PYTHON_BIN,
        "-u",
        str(script_path),
        "--main-model",
        payload.main_model,
        "--proposer-model",
        payload.proposer_model,
        "--num-tasks",
        str(payload.num_tasks),
        "--num-test-tasks",
        str(payload.num_test_tasks),
        "--batch-size",
        str(payload.batch_size),
        "--session-id",
        session_id,
    ]
    if not payload.on_policy:
        # Script only has --on-policy flag (default True), so we keep default behavior.
        # This value is kept for UI compatibility and future extension.
        pass
    if not payload.eval_vanilla:
        command.append("--no-eval-vanilla")
    if not payload.eval_evolved:
        command.append("--no-eval-evolved")
    return command, script_path


def _sse_format(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_log_progress(log_path: Path) -> tuple[int, int]:
    if not log_path.exists():
        return (0, 0)
    try:
        all_lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return (0, 0)
    latest: tuple[int, int] = (0, 0)
    for line in all_lines:
        match = _BATCH_PROGRESS_RE.search(line)
        if match:
            latest = (int(match.group(1)), int(match.group(2)))
    return latest


def _artifact_counts(session_path: Path) -> dict[str, int]:
    registry = _read_json(session_path / "registry.json", {"tools": []})
    knowledge = _read_json(session_path / "knowledge.json", [])
    patches = _read_json(session_path / "patches.json", [])
    skills_dir = session_path / "skills"
    skills = list(skills_dir.glob("*.md")) if skills_dir.exists() else []
    return {
        "tools": len(registry.get("tools", [])),
        "skills": len(skills),
        "knowledge": len(knowledge),
        "patches": len(patches),
    }


def _line_has_eval_marker(line: str) -> bool:
    content = line.lower()
    return (
        "testing phase" in content
        or "final comparison" in content
        or "vanilla agent results" in content
        or "evolved agent results" in content
    )


def _extract_stage_event(line: str) -> dict[str, Any] | None:
    lowered = line.lower()
    stage_num: str | None = None
    stage_name: str | None = None
    status: str | None = None
    action: str | None = None

    if _DIAGNOSIS_RE.search(line):
        stage_num, stage_name = "1", "diagnosis"
    elif _PLAN_RE.search(line):
        stage_num, stage_name = "2", "plan"
    elif _APPLY_RE.search(line):
        stage_num, stage_name = "3", "apply"
    elif _VERIFY_RE.search(line):
        stage_num, stage_name = "4", "verify"
    else:
        stage_match = _STAGE_RE.search(line)
        if stage_match:
            stage_num = stage_match.group(1)
            stage_name = {
                "1": "diagnosis",
                "2": "plan",
                "3": "apply",
                "4": "verify",
            }.get(stage_num)

    action_match = _PROPOSAL_ACTION_RE.search(line)
    if action_match:
        action = action_match.group(1).strip()
        if stage_name is None:
            stage_name = "apply"
            stage_num = stage_num or "3"

    verified_match = _VERIFIED_RE.search(line)
    if verified_match:
        stage_name = "verify"
        stage_num = stage_num or "4"
        if "reverted" in lowered or "failed" in lowered:
            status = "failed"
        else:
            status = "completed"

    if stage_name is None and action is None:
        return None

    payload: dict[str, Any] = {"line": line}
    if stage_num is not None:
        payload["stage"] = stage_num
    if stage_name is not None:
        payload["stage_name"] = stage_name
    if status is not None:
        payload["status"] = status
    if action is not None:
        payload["action"] = action
    return payload


def _extract_task_event(line: str) -> dict[str, Any] | None:
    """Extract real-time task/step progress events from a log line."""
    task_start = _TASK_START_RE.search(line)
    if task_start:
        return {
            "type": "task_start",
            "task_num": int(task_start.group(1)),
            "total_tasks": int(task_start.group(2)),
            "task_id": task_start.group(3),
        }

    step_match = _STEP_PROGRESS_RE.search(line)
    if step_match:
        return {
            "type": "step",
            "step_num": int(step_match.group(1)),
            "total_steps": int(step_match.group(2)),
        }

    task_done = _TASK_DONE_RE.search(line)
    if task_done:
        return {
            "type": "task_done",
            "task_id": task_done.group(1),
            "score": float(task_done.group(2)),
            "steps": int(task_done.group(3)),
        }

    return None


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _session_path(session_id: str) -> Path:
    session_path = SESSIONS_DIR / session_id
    if not session_path.exists() or not session_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return session_path


def _batch_path(session_path: Path, batch_id: str) -> Path:
    normalized = batch_id.replace(".jsonl", "")
    if not normalized.startswith("batch_"):
        normalized = f"batch_{normalized}"
    path = session_path / "observations" / f"{normalized}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")
    return path


def _parse_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for idx, line in enumerate(file):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
                rows.append(data)
            except json.JSONDecodeError:
                rows.append({"_parse_error": True, "_line": idx + 1})
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _parse_report_counts(report_text: str | None) -> dict[str, int]:
    if not report_text:
        return {"passed": 0, "failed": 0, "total": 0}

    passed_match = _PASSED_TESTS_RE.search(report_text)
    failed_match = _FAILED_TESTS_RE.search(report_text)
    total_match = _TOTAL_TESTS_RE.search(report_text)

    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    total = int(total_match.group(1)) if total_match else 0
    return {"passed": passed, "failed": failed, "total": total}


def _collect_eval_variant(session_id: str, variant: str) -> dict[str, Any]:
    output_dir = EXPERIMENTS_DIR / f"{session_id}_{variant}"
    if not output_dir.exists() or not output_dir.is_dir():
        return {
            "name": variant,
            "session_id": session_id,
            "path": None,
            "tasks": [],
            "summary": {
                "num_tasks": 0,
                "avg_score": 0.0,
                "total_passed_tests": 0,
                "total_failed_tests": 0,
                "total_tests": 0,
            },
        }

    tasks_dir = output_dir / "tasks"
    task_rows: list[dict[str, Any]] = []
    if tasks_dir.exists():
        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            report_text = _read_text(task_dir / "evaluation" / "report.md")
            counts = _parse_report_counts(report_text)
            score = (counts["passed"] / counts["total"]) if counts["total"] > 0 else 0.0
            task_rows.append(
                {
                    "task_id": task_id,
                    "score": score,
                    "num_passed_tests": counts["passed"],
                    "num_failed_tests": counts["failed"],
                    "num_total_tests": counts["total"],
                    "report": report_text,
                }
            )

    total_tasks = len(task_rows)
    avg_score = (
        sum(float(row.get("score", 0.0)) for row in task_rows) / total_tasks
        if total_tasks
        else 0.0
    )
    total_passed_tests = sum(int(row.get("num_passed_tests", 0)) for row in task_rows)
    total_failed_tests = sum(int(row.get("num_failed_tests", 0)) for row in task_rows)
    total_tests = sum(int(row.get("num_total_tests", 0)) for row in task_rows)

    return {
        "name": variant,
        "session_id": session_id,
        "path": str(output_dir),
        "tasks": task_rows,
        "summary": {
            "num_tasks": total_tasks,
            "avg_score": avg_score,
            "total_passed_tests": total_passed_tests,
            "total_failed_tests": total_failed_tests,
            "total_tests": total_tests,
        },
    }


_BATCH_SIZE_RE = re.compile(r"^Batch\s+size:\s*(\d+)", re.IGNORECASE | re.MULTILINE)


def _parse_batch_size(session_path: Path) -> int | None:
    """Try to extract the batch_size from the evolve.log header."""
    log_path = session_path / "evolve.log"
    if not log_path.exists():
        return None
    try:
        # Read just the first 2KB of the log to find the header section
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            header = f.read(2048)
        match = _BATCH_SIZE_RE.search(header)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def _list_batches(session_path: Path) -> list[dict[str, Any]]:
    obs_dir = session_path / "observations"
    if not obs_dir.exists():
        return []

    batch_files = sorted(obs_dir.glob("batch_*.jsonl"))
    if not batch_files:
        return []

    batch_size = _parse_batch_size(session_path)

    # If there are already multiple files, use them as-is (no splitting needed)
    if len(batch_files) > 1 or batch_size is None:
        batches: list[dict[str, Any]] = []
        for batch_file in batch_files:
            rows = _parse_jsonl(batch_file)
            scores = [float(r.get("score", 0.0)) for r in rows if isinstance(r, dict)]
            batches.append(
                {
                    "id": batch_file.stem,
                    "file": str(batch_file),
                    "num_tasks": len(rows),
                    "average_score": (sum(scores) / len(scores)) if scores else 0.0,
                    "updated_at": datetime.fromtimestamp(
                        batch_file.stat().st_mtime
                    ).isoformat(),
                }
            )
        return batches

    # Single file + known batch_size: split into virtual batches (complete ones only)
    single_file = batch_files[0]
    all_rows = _parse_jsonl(single_file)
    total_rows = len(all_rows)
    num_complete = total_rows // batch_size
    has_partial = total_rows % batch_size > 0

    # Check if we should include the partial last batch (when the run is finished)
    current_batch, total_batches = _parse_log_progress(session_path / "evolve.log")
    include_partial = has_partial and current_batch >= total_batches and total_batches > 0

    if num_complete == 0 and not include_partial:
        # Not enough rows for even one complete batch
        if total_rows > 0:
            # Still show it as batch_000 with partial data (better than nothing)
            scores = [float(r.get("score", 0.0)) for r in all_rows if isinstance(r, dict)]
            return [
                {
                    "id": single_file.stem,
                    "file": str(single_file),
                    "num_tasks": total_rows,
                    "average_score": (sum(scores) / len(scores)) if scores else 0.0,
                    "updated_at": datetime.fromtimestamp(
                        single_file.stat().st_mtime
                    ).isoformat(),
                }
            ]
        return []

    num_to_show = num_complete + (1 if include_partial else 0)
    batches = []
    mtime_iso = datetime.fromtimestamp(single_file.stat().st_mtime).isoformat()
    for vi in range(num_to_show):
        start = vi * batch_size
        chunk = all_rows[start : start + batch_size]
        batch_id = f"batch_{vi:03d}"
        scores = [float(r.get("score", 0.0)) for r in chunk if isinstance(r, dict)]
        batches.append(
            {
                "id": batch_id,
                "file": str(single_file),
                "num_tasks": len(chunk),
                "average_score": (sum(scores) / len(scores)) if scores else 0.0,
                "updated_at": mtime_iso,
            }
        )
    return batches


def _session_summary(session_path: Path) -> dict[str, Any]:
    summary_stats = _read_json(session_path / "summary_stats.json", {})
    results = _read_json(session_path / "results.json", None)
    registry = _read_json(session_path / "registry.json", {"tools": []})
    knowledge = _read_json(session_path / "knowledge.json", [])
    patches = _read_json(session_path / "patches.json", [])

    skills_dir = session_path / "skills"
    skill_files = sorted(skills_dir.glob("*.md")) if skills_dir.exists() else []
    batches = _list_batches(session_path)

    return {
        "id": session_path.name,
        "path": str(session_path),
        "created_at": datetime.fromtimestamp(session_path.stat().st_ctime).isoformat(),
        "updated_at": datetime.fromtimestamp(session_path.stat().st_mtime).isoformat(),
        "summary_stats": summary_stats,
        "results": results,
        "counts": {
            "batches": len(batches),
            "tools": len(registry.get("tools", [])),
            "skills": len(skill_files),
            "knowledge": len(knowledge),
            "patches": len(patches),
        },
    }


app = FastAPI(title="A-Evolve Visual API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "sessions_dir": str(SESSIONS_DIR),
        "experiments_dir": str(EXPERIMENTS_DIR),
        "evolve_python_bin": EVOLVE_PYTHON_BIN,
    }


@app.get("/api/sessions")
def list_sessions() -> dict[str, Any]:
    if not SESSIONS_DIR.exists():
        return {"sessions": []}

    session_dirs = [p for p in SESSIONS_DIR.iterdir() if p.is_dir()]
    session_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    sessions = [_session_summary(p) for p in session_dirs]
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    return _session_summary(session_path)


@app.get("/api/sessions/{session_id}/stats")
def get_stats(session_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    return {
        "session_id": session_id,
        "stats": _read_json(session_path / "summary_stats.json", {}),
    }


@app.get("/api/sessions/{session_id}/batches")
def get_batches(session_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    return {"session_id": session_id, "batches": _list_batches(session_path)}


@app.get("/api/sessions/{session_id}/batches/{batch_id}")
def get_batch(
    session_id: str,
    batch_id: str,
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> dict[str, Any]:
    session_path = _session_path(session_id)
    obs_dir = session_path / "observations"

    # Normalise requested batch_id
    normalized = batch_id.replace(".jsonl", "")
    if not normalized.startswith("batch_"):
        normalized = f"batch_{normalized}"

    direct_path = obs_dir / f"{normalized}.jsonl"
    batch_size = _parse_batch_size(session_path)
    batch_files = sorted(obs_dir.glob("batch_*.jsonl")) if obs_dir.exists() else []

    # If only a single physical file and we need virtual splitting
    if not direct_path.exists() and len(batch_files) == 1 and batch_size is not None:
        all_rows = _parse_jsonl(batch_files[0])
        # Parse the numeric batch index from batch_id like "batch_001" -> 1
        try:
            batch_idx = int(normalized.split("_", 1)[1])
        except (ValueError, IndexError):
            raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")
        start = batch_idx * batch_size
        if start >= len(all_rows):
            raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")
        rows = all_rows[start : start + batch_size]
    else:
        if not direct_path.exists():
            raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")
        all_rows = _parse_jsonl(direct_path)
        # If single file + virtual batches, slice it for batch_000 too
        if batch_size is not None and len(batch_files) == 1 and len(all_rows) > batch_size:
            rows = all_rows[:batch_size]
        else:
            rows = all_rows

    if limit is not None:
        rows = rows[:limit]

    return {
        "session_id": session_id,
        "batch_id": normalized,
        "observations": rows,
    }


@app.get("/api/sessions/{session_id}/tasks/{task_id}")
def get_task(session_id: str, task_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    obs_dir = session_path / "observations"

    found: dict[str, Any] | None = None
    source_batch: str | None = None
    if obs_dir.exists():
        for batch_file in sorted(obs_dir.glob("batch_*.jsonl")):
            for row in _parse_jsonl(batch_file):
                if row.get("task_id") == task_id:
                    found = row
                    source_batch = batch_file.stem
                    break
            if found is not None:
                break

    if found is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    task_dir = EXPERIMENTS_DIR / session_id / "tasks" / task_id
    evaluation_report = _read_text(task_dir / "evaluation" / "report.md")
    environment_io = _read_text(task_dir / "logs" / "environment_io.md")
    api_calls = _read_text(task_dir / "logs" / "api_calls.jsonl")

    return {
        "session_id": session_id,
        "task_id": task_id,
        "batch_id": source_batch,
        "observation": found,
        "artifacts": {
            "task_dir": str(task_dir),
            "evaluation_report": evaluation_report,
            "environment_io": environment_io,
            "api_calls_jsonl": api_calls,
        },
    }


@app.get("/api/sessions/{session_id}/tools")
def get_tools(session_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    registry = _read_json(session_path / "registry.json", {"tools": []})
    tools: list[dict[str, Any]] = []
    for tool in registry.get("tools", []):
        tool_copy = dict(tool)
        path = tool_copy.get("file_path")
        if isinstance(path, str) and path:
            tool_copy["source"] = _read_text(Path(path))
        else:
            tool_copy["source"] = None
        tools.append(tool_copy)
    return {"session_id": session_id, "tools": tools}


@app.get("/api/sessions/{session_id}/skills")
def get_skills(session_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    skills_dir = session_path / "skills"
    skills: list[dict[str, Any]] = []
    if skills_dir.exists():
        for skill_file in sorted(skills_dir.glob("*.md")):
            skills.append(
                {
                    "name": skill_file.stem,
                    "path": str(skill_file),
                    "content": _read_text(skill_file),
                }
            )
    return {"session_id": session_id, "skills": skills}


@app.get("/api/sessions/{session_id}/knowledge")
def get_knowledge(session_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    return {
        "session_id": session_id,
        "knowledge": _read_json(session_path / "knowledge.json", []),
    }


@app.get("/api/sessions/{session_id}/patches")
def get_patches(session_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    return {"session_id": session_id, "patches": _read_json(session_path / "patches.json", [])}


@app.get("/api/sessions/{session_id}/pipeline/{batch_id}")
def get_pipeline(session_id: str, batch_id: str) -> dict[str, Any]:
    session_path = _session_path(session_id)
    obs_dir = session_path / "observations"

    # Normalise batch_id
    normalized = batch_id.replace(".jsonl", "")
    if not normalized.startswith("batch_"):
        normalized = f"batch_{normalized}"

    # Resolve batch_idx
    batch_idx_value: int | None = None
    try:
        batch_idx_value = int(normalized.split("_", 1)[1])
    except (ValueError, IndexError):
        batch_idx_value = None

    # Get rows for this (possibly virtual) batch
    direct_path = obs_dir / f"{normalized}.jsonl" if obs_dir.exists() else None
    batch_files = sorted(obs_dir.glob("batch_*.jsonl")) if obs_dir and obs_dir.exists() else []
    evo_batch_size = _parse_batch_size(session_path)

    if direct_path and direct_path.exists():
        all_file_rows = _parse_jsonl(direct_path)
        if evo_batch_size and len(batch_files) == 1 and len(all_file_rows) > evo_batch_size and batch_idx_value is not None:
            start = batch_idx_value * evo_batch_size
            rows = all_file_rows[start : start + evo_batch_size]
        else:
            rows = all_file_rows
    elif len(batch_files) == 1 and evo_batch_size and batch_idx_value is not None:
        all_file_rows = _parse_jsonl(batch_files[0])
        start = batch_idx_value * evo_batch_size
        if start >= len(all_file_rows):
            raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")
        rows = all_file_rows[start : start + evo_batch_size]
    else:
        raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")

    stage_history: list[dict[str, Any]] = []
    for row in rows:
        if "_stage_history" in row:
            value = row.get("_stage_history")
            if isinstance(value, list):
                stage_history.extend(value)

    proposal_cycles: list[dict[str, Any]] = []
    proposals_path = session_path / "proposals.jsonl"
    if proposals_path.exists():
        all_proposals = _parse_jsonl(proposals_path)
        for proposal in all_proposals:
            if not isinstance(proposal, dict) or proposal.get("_parse_error"):
                continue
            proposal_batch_id = proposal.get("batch_id")
            proposal_batch_idx = proposal.get("batch_idx")
            if proposal_batch_id == normalized:
                proposal_cycles.append(proposal)
                continue
            if batch_idx_value is not None and isinstance(proposal_batch_idx, int):
                if proposal_batch_idx == batch_idx_value:
                    proposal_cycles.append(proposal)

    snapshots_dir = session_path / "version_snapshots"
    snapshots = []
    if snapshots_dir.exists():
        for snap_file in sorted(snapshots_dir.glob("*.json")):
            snap_json = _read_json(snap_file, {})
            snapshots.append(
                {
                    "version_id": snap_json.get("version_id", snap_file.stem),
                    "timestamp": snap_json.get("timestamp"),
                    "description": snap_json.get("description"),
                    "is_committed": snap_json.get("is_committed"),
                    "is_reverted": snap_json.get("is_reverted"),
                }
            )

    return {
        "session_id": session_id,
        "batch_id": normalized,
        "stage_history": stage_history,
        "proposal_cycles": proposal_cycles,
        "version_snapshots": snapshots,
        "has_explicit_stage_history": len(stage_history) > 0,
    }


@app.get("/api/sessions/{session_id}/evaluations")
def get_evaluations(session_id: str) -> dict[str, Any]:
    # Validate the session first to provide a clear 404 for unknown sessions.
    _ = _session_path(session_id)

    vanilla = _collect_eval_variant(session_id, "vanilla")
    evolved = _collect_eval_variant(session_id, "evolved")

    vanilla_by_task = {item["task_id"]: item for item in vanilla["tasks"]}
    evolved_by_task = {item["task_id"]: item for item in evolved["tasks"]}
    task_ids = sorted(set(vanilla_by_task.keys()) | set(evolved_by_task.keys()))
    comparison: list[dict[str, Any]] = []
    for task_id in task_ids:
        vanilla_task = vanilla_by_task.get(task_id)
        evolved_task = evolved_by_task.get(task_id)
        vanilla_score = (
            float(vanilla_task.get("score", 0.0)) if isinstance(vanilla_task, dict) else None
        )
        evolved_score = (
            float(evolved_task.get("score", 0.0)) if isinstance(evolved_task, dict) else None
        )
        delta = (
            (evolved_score - vanilla_score)
            if vanilla_score is not None and evolved_score is not None
            else None
        )
        comparison.append(
            {
                "task_id": task_id,
                "vanilla_score": vanilla_score,
                "evolved_score": evolved_score,
                "delta": delta,
            }
        )

    return {
        "session_id": session_id,
        "vanilla": vanilla,
        "evolved": evolved,
        "comparison": comparison,
    }


@app.post("/api/evolve/start")
def evolve_start(payload: EvolveStartRequest) -> dict[str, Any]:
    if payload.environment.lower() != "appworld":
        raise HTTPException(status_code=400, detail="Only appworld environment is supported")
    if payload.main_model not in MODEL_OPTIONS:
        raise HTTPException(status_code=400, detail="Unsupported main_model")
    if payload.proposer_model not in MODEL_OPTIONS:
        raise HTTPException(status_code=400, detail="Unsupported proposer_model")

    active = _running_run()
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A run is already active: {active.session_id}",
        )

    required_env = _required_api_env(payload.main_model)
    # Use the key from the UI if provided, otherwise check the server env
    effective_api_key = (payload.api_key or "").strip() or os.getenv(required_env, "")
    if not effective_api_key:
        raise HTTPException(
            status_code=400,
            detail=f"Missing API key. Either enter it in the UI or set {required_env} in the server environment.",
        )

    session_id = _session_id_from_model(payload.main_model, payload.num_tasks)
    session_path = SESSIONS_DIR / session_id
    session_path.mkdir(parents=True, exist_ok=True)
    log_path = session_path / "evolve.log"
    log_path.touch(exist_ok=True)

    command, script_path = _build_start_command(payload, session_id)
    env = os.environ.copy()
    env.setdefault("APPWORLD_ROOT", str(PROJECT_ROOT / "agentic-evolution"))
    # Inject the API key into the subprocess environment
    env[required_env] = effective_api_key

    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(script_path.parent),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

    RUNS[session_id] = EvolveRun(
        session_id=session_id,
        environment=payload.environment,
        main_model=payload.main_model,
        proposer_model=payload.proposer_model,
        process=process,
        command=command,
        started_at=time.time(),
        log_path=log_path,
    )

    return {
        "session_id": session_id,
        "status": "started",
        "pid": process.pid,
        "log_path": str(log_path),
    }


@app.get("/api/evolve/{session_id}/status")
def evolve_status(session_id: str) -> dict[str, Any]:
    run = RUNS.get(session_id)
    session_path = SESSIONS_DIR / session_id

    if run is None:
        if not session_path.exists():
            raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
        return {
            "session_id": session_id,
            "status": "completed",
            "pid": None,
            "elapsed_seconds": 0,
            "current_batch": len(_list_batches(session_path)),
            "total_batches": len(_list_batches(session_path)),
            "artifact_counts": _artifact_counts(session_path),
        }

    _refresh_run_status(run)
    current_batch, total_batches = _parse_log_progress(run.log_path)
    if current_batch == 0:
        current_batch = len(_list_batches(session_path))
    if total_batches == 0:
        total_batches = max(current_batch, len(_list_batches(session_path)))

    return {
        "session_id": session_id,
        "status": run.status,
        "pid": run.process.pid,
        "elapsed_seconds": int(time.time() - run.started_at),
        "current_batch": current_batch,
        "total_batches": total_batches,
        "return_code": run.return_code,
        "error": run.error,
        "artifact_counts": _artifact_counts(session_path),
    }


@app.post("/api/evolve/{session_id}/stop")
def evolve_stop(session_id: str) -> dict[str, Any]:
    run = RUNS.get(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown running session: {session_id}")

    _refresh_run_status(run)
    if run.status != "running":
        return {"session_id": session_id, "status": run.status, "message": "Run already stopped"}

    run.process.terminate()
    try:
        run.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        run.process.kill()
        run.process.wait(timeout=5)

    run.status = "failed"
    run.return_code = run.process.returncode
    run.error = "stopped_by_user"
    return {"session_id": session_id, "status": "stopped"}


@app.get("/api/evolve/{session_id}/stream")
async def evolve_stream(session_id: str) -> StreamingResponse:
    run = RUNS.get(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown running session: {session_id}")

    session_path = SESSIONS_DIR / session_id
    log_path = run.log_path

    async def event_generator():
        pointer = 0
        known_batches: dict[str, float] = {}
        known_proposals_count = 0
        artifact_signature = ""
        status_tick = 0
        proposals_path = session_path / "proposals.jsonl"

        while True:
            _refresh_run_status(run)

            if log_path.exists():
                stage_events_this_tick = 0
                with log_path.open("r", encoding="utf-8", errors="ignore") as stream:
                    stream.seek(pointer)
                    for raw_line in stream:
                        line = raw_line.rstrip("\n")
                        yield _sse_format("log", {"line": line})
                        stage_payload = _extract_stage_event(line)
                        if stage_payload:
                            stage_events_this_tick += 1
                            yield _sse_format("stage", stage_payload)
                        task_payload = _extract_task_event(line)
                        if task_payload:
                            yield _sse_format("task_progress", task_payload)
                        if _line_has_eval_marker(line):
                            yield _sse_format("eval_result", {"line": line})
                    pointer = stream.tell()

            if proposals_path.exists():
                proposal_rows = _parse_jsonl(proposals_path)
                if known_proposals_count < len(proposal_rows):
                    for proposal_row in proposal_rows[known_proposals_count:]:
                        if isinstance(proposal_row, dict) and not proposal_row.get("_parse_error"):
                            yield _sse_format("proposal_update", proposal_row)
                    known_proposals_count = len(proposal_rows)

            obs_dir = session_path / "observations"
            if obs_dir.exists():
                batch_files = sorted(obs_dir.glob("batch_*.jsonl"))
                evo_batch_size = _parse_batch_size(session_path)

                if len(batch_files) == 1 and evo_batch_size is not None:
                    # Virtual batch splitting: only emit COMPLETE batches
                    single_file = batch_files[0]
                    mtime = single_file.stat().st_mtime
                    row_count = sum(1 for line in single_file.open("r", encoding="utf-8", errors="ignore") if line.strip())
                    # Floor division: only count batches that have all their rows
                    num_complete = row_count // evo_batch_size
                    # Also include partial last batch if the run has ended
                    has_partial = row_count % evo_batch_size > 0
                    if has_partial and run.status != "running":
                        num_complete += 1
                    for vi in range(num_complete):
                        vkey = f"batch_{vi:03d}"
                        if vkey not in known_batches or known_batches[vkey] < mtime:
                            known_batches[vkey] = mtime
                            yield _sse_format(
                                "batch_complete",
                                {"batch_id": vkey, "updated_at": datetime.fromtimestamp(mtime).isoformat()},
                            )
                else:
                    for batch_file in batch_files:
                        mtime = batch_file.stat().st_mtime
                        key = batch_file.stem
                        if key not in known_batches or known_batches[key] < mtime:
                            known_batches[key] = mtime
                            yield _sse_format(
                                "batch_complete",
                                {"batch_id": key, "updated_at": datetime.fromtimestamp(mtime).isoformat()},
                            )

            current_artifacts = _artifact_counts(session_path)
            signature = json.dumps(current_artifacts, sort_keys=True)
            if signature != artifact_signature:
                artifact_signature = signature
                yield _sse_format("artifact_change", current_artifacts)

            status_tick += 1
            if status_tick % 2 == 0:
                current_batch, total_batches = _parse_log_progress(log_path)
                batch_file_count = len(_list_batches(session_path))
                if current_batch == 0:
                    current_batch = batch_file_count
                if total_batches == 0:
                    total_batches = max(current_batch, batch_file_count)
                yield _sse_format(
                    "status",
                    {
                        "session_id": session_id,
                        "status": run.status,
                        "elapsed_seconds": int(time.time() - run.started_at),
                        "current_batch": current_batch,
                        "total_batches": total_batches,
                    },
                )

            if run.status != "running":
                yield _sse_format(
                    "done",
                    {
                        "session_id": session_id,
                        "status": run.status,
                        "return_code": run.return_code,
                        "error": run.error,
                    },
                )
                break

            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

