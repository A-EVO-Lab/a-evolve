"""Observer -- collects (task, trajectory, feedback) triples into structured logs.

Adapted from agentic-evolution/modules/observer/persistent_observer.py.
Writes JSONL batch files for the evolver to analyze.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..types import Observation

logger = logging.getLogger(__name__)


def _parse_iso_tolerant(raw: Any) -> datetime | None:
    """Parse an ISO-ish string or pass through a ``datetime``.

    Accepts ``datetime``, naive or tz-aware ISO strings, and the
    Zulu ``Z`` shorthand.  Returns ``None`` for empty / unparseable
    values.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _label_revealed(obs: Observation, batch_ts: datetime | None) -> bool:
    """True iff a task's ground-truth label should be included in the JSONL.

    Tasks WITHOUT a resolution_date (e.g. CTF challenges) are immediately
    revealed — there is no temporal ordering to protect.
    Tasks WITH a resolution_date are revealed iff resolution <= batch_ts.
    """
    meta = obs.task.metadata or {}
    raw = meta.get("resolution_date") or meta.get("resolved_at")
    resolved = _parse_iso_tolerant(raw)
    if resolved is None:
        return True
    if batch_ts is None:
        return False
    if not batch_ts.tzinfo:
        batch_ts = batch_ts.replace(tzinfo=timezone.utc)
    return resolved <= batch_ts


def render_reveal_update(
    cycle: int,
    watermark: datetime | None,
    in_batch_revealed: list[dict[str, Any]],
    newly_revealed_past: list[dict[str, Any]],
    total_revealed: int,
    total_past: int,
) -> str:
    """ASCII cumulative reveal update.

    Two reveal channels are reported each cycle:

    * **In-batch reveals** — tasks from the *current* batch whose
      resolution already slipped under the watermark at
      observer.collect() time (labels included directly in the batch
      JSONL).
    * **Newly revealed past tasks** — tasks from *earlier* batches
      whose resolution has now crossed the watermark; labels are
      appended to ``revealed_supplement.jsonl`` and overlaid on top
      of the stored batch JSONL when the evolver reads it.

    ``in_batch_revealed`` and ``newly_revealed_past`` are each lists
    of dicts with ``task_id``, ``creation_date`` (datetime | None),
    ``resolution_date`` (datetime | None).
    """
    def _fmt_date(d: datetime | None) -> str:
        return d.strftime("%b %d %Y") if d is not None else "—"

    def _render_bucket(label: str, items: list[dict[str, Any]]) -> list[str]:
        out = [f"  {label} ({len(items)}):"]
        for r in items:
            out.append(
                f"    ●  {r['task_id']:<40s}  "
                f"create={_fmt_date(r.get('creation_date'))}  "
                f"resolve={_fmt_date(r.get('resolution_date'))}"
            )
        return out

    wm = "disabled" if watermark is None else watermark.strftime("%Y-%m-%d")
    lines = [f"Reveal update at cycle {cycle} (watermark = {wm}):"]
    lines += _render_bucket("In-batch reveals", in_batch_revealed)
    lines += _render_bucket("Newly revealed past tasks", newly_revealed_past)
    still_pending = max(total_past - total_revealed, 0)
    lines.append(
        f"  Total revealed: {total_revealed} / {total_past} past tasks  "
        f"({still_pending} still pending)"
    )
    return "\n".join(lines)


class Observer:
    """Collects observations and persists them as JSONL in the evolution/ directory.

    V2 adds:
    - ``trajectory_only``: when True, suppress ground-truth signals
      (``success``/``score``/``feedback_detail``/``feedback``) so the
      evolver must infer improvement opportunities from behaviour alone.
    - ``temporal_reveal``: orthogonal flag.  When True, the observer
      decides per-task whether to include feedback based on whether
      the task's resolution date is on or before the current batch
      timestamp (set via ``set_batch_timestamp``).  ``temporal_reveal``
      overrides ``trajectory_only`` per-task — it is the new realistic
      drip-of-truth mode.
    - Per-task artifacts on disk (``patch_<tid>.diff`` + ``trajectory_<tid>.json``)
      so downstream analysis scripts can open individual task outputs
      without streaming the whole batch JSONL.
    """

    def __init__(
        self,
        evolution_dir: str | Path,
        *,
        trajectory_only: bool = False,
        temporal_reveal: bool = False,
    ):
        self.evolution_dir = Path(evolution_dir)
        self.trajectory_only = trajectory_only
        self.temporal_reveal = temporal_reveal
        self._current_batch_ts: datetime | None = None
        # Ground-truth-bearing JSONL (success/score/feedback): framework-only.
        self.observations_dir = self.evolution_dir / "observations"
        self.observations_dir.mkdir(parents=True, exist_ok=True)
        # Safe per-task artifacts (conversation + patch only): mountable into
        # the evolver sandbox without leaking evaluation signals.
        self.trajectories_dir = self.evolution_dir / "trajectories"
        self.trajectories_dir.mkdir(parents=True, exist_ok=True)
        # Temporal-reveal state (only touched when temporal_reveal=True):
        #   _archive_path    — every task's feedback + resolution_date, always
        #                      written.  Used to look up feedback for tasks
        #                      that were pending at batch write-time and
        #                      become revealed in a later cycle.
        #   _supplement_path — append-only file of newly-revealed tasks.
        #                      Prompt builders union this with batch JSONL.
        #   _revealed_ids    — set of task_ids already emitted to supplement,
        #                      so each task is revealed at most once.
        self._archive_path = self.evolution_dir / "feedback_archive.jsonl"
        self._supplement_path = self.observations_dir / "revealed_supplement.jsonl"
        self._revealed_ids = self._load_revealed_ids()
        # In-batch reveals recorded during the latest ``collect()`` call,
        # consumed by the following ``update_reveal_state()``.
        self._last_in_batch_reveals: list[dict[str, Any]] = []
        self._batch_id = self._next_batch_id()

    def _load_revealed_ids(self) -> set[str]:
        """Populate the revealed set from any existing supplement file
        (for resuming a run mid-sweep)."""
        ids: set[str] = set()
        if not self._supplement_path.exists():
            return ids
        with open(self._supplement_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    tid = r.get("task_id")
                    if isinstance(tid, str):
                        ids.add(tid)
                except Exception:
                    pass
        return ids

    def set_batch_timestamp(self, ts: datetime | None) -> None:
        """Set the simulated 'now' for the next ``collect()`` call.

        Only used when ``temporal_reveal=True``.  Pass ``None`` to
        disable the per-task gate (reverts to the legacy
        ``trajectory_only`` path).
        """
        self._current_batch_ts = ts

    def collect(self, observations: list[Observation]) -> Path:
        """Write a batch of observations to a JSONL file and per-task artifacts.

        In ``temporal_reveal`` mode, also appends to an internal
        ``feedback_archive.jsonl`` so tasks whose label was pending at
        batch-write time can be surfaced via
        ``update_reveal_state()`` in a later cycle when their
        resolution date falls below the watermark.
        """
        batch_file = self.observations_dir / f"batch_{self._batch_id:04d}.jsonl"
        tasks_dir = self.trajectories_dir / f"batch_{self._batch_id:04d}"
        tasks_dir.mkdir(parents=True, exist_ok=True)

        archive_f = None
        if self.temporal_reveal:
            archive_f = open(self._archive_path, "a")
            # Reset the in-batch-reveals bucket for this collect() call.
            self._last_in_batch_reveals = []

        try:
            with open(batch_file, "w") as f:
                for obs in observations:
                    tid = obs.task.id.replace("/", "_")
                    # Per-task artifacts (V2)
                    (tasks_dir / f"patch_{tid}.diff").write_text(obs.trajectory.output or "")
                    if obs.trajectory.conversation:
                        (tasks_dir / f"trajectory_{tid}.json").write_text(
                            json.dumps(obs.trajectory.conversation, indent=2, ensure_ascii=False))

                    # Claim-level feedback from raw data
                    claims = []
                    if obs.feedback.raw and "per_claim" in obs.feedback.raw:
                        for claim_data in obs.feedback.raw["per_claim"]:
                            claims.append({
                                "claim": claim_data.get("claim", ""),
                                "outcome": claim_data.get("outcome", "not_fulfilled"),
                                "pass": claim_data.get("score", 0.0) >= 1.0,
                                "score": claim_data.get("score", 0.0),
                                "justification": claim_data.get("justification", ""),
                            })

                    # Save in nested format for stratified engine
                    record: dict[str, Any] = {
                        # Flat fields for backward compatibility
                        "task_id": obs.task.id,
                        "batch": self._batch_id,
                        "task_input": obs.task.input,
                        "agent_output": obs.trajectory.output,
                        "steps": obs.trajectory.steps,
                        "conversation": obs.trajectory.conversation,
                        "timestamp": datetime.now().isoformat(),

                        # Nested structure for new engines
                        "task": {
                            "id": obs.task.id,
                            "input": obs.task.input,
                            "metadata": obs.task.metadata,
                        },
                        "trajectory": {
                            "output": obs.trajectory.output,
                            "steps": obs.trajectory.steps,
                        },
                    }

                    if self.temporal_reveal:
                        # Always append feedback to the internal archive so
                        # it can be surfaced later when the watermark
                        # advances.  This file is never read by the evolver
                        # directly; the evolver sees revealed_supplement.jsonl.
                        meta = obs.task.metadata or {}
                        archive_f.write(json.dumps({  # type: ignore[union-attr]
                            "task_id": obs.task.id,
                            "batch_id": self._batch_id,
                            "resolution_date": meta.get("resolution_date")
                                               or meta.get("resolved_at"),
                            "creation_date": meta.get("creation_date")
                                             or meta.get("timestamp"),
                            "success": obs.feedback.success,
                            "score": obs.feedback.score,
                            "feedback_detail": obs.feedback.detail,
                            "feedback": {
                                "success": obs.feedback.success,
                                "score": obs.feedback.score,
                                "detail": obs.feedback.detail,
                                "claims": claims,
                                "raw": obs.feedback.raw,
                            },
                        }, default=str) + "\n")

                        # In-batch reveal gate: include feedback in the batch
                        # JSONL immediately for tasks that have already
                        # resolved by the current watermark.  Tasks still
                        # pending get surfaced later via the supplement.
                        include = _label_revealed(obs, self._current_batch_ts)
                        if include:
                            self._revealed_ids.add(obs.task.id)
                            self._last_in_batch_reveals.append({
                                "task_id": obs.task.id,
                                "creation_date": _parse_iso_tolerant(
                                    meta.get("creation_date")
                                    or meta.get("timestamp")),
                                "resolution_date": _parse_iso_tolerant(
                                    meta.get("resolution_date")
                                    or meta.get("resolved_at")),
                            })
                    else:
                        include = not self.trajectory_only

                    if include:
                        record["success"] = obs.feedback.success
                        record["score"] = obs.feedback.score
                        record["feedback_detail"] = obs.feedback.detail
                        record["feedback"] = {
                            "success": obs.feedback.success,
                            "score": obs.feedback.score,
                            "detail": obs.feedback.detail,
                            "claims": claims,
                            "raw": obs.feedback.raw,
                        }
                    f.write(json.dumps(record, default=str) + "\n")
        finally:
            if archive_f is not None:
                archive_f.close()

        logger.info("Wrote %d observations to %s (artifacts in %s/)",
                    len(observations), batch_file.name, tasks_dir.name)
        self._batch_id += 1
        return batch_file

    # ── Reveal-gate API ───────────────────────────────────────────────
    # These helpers answer "may the evolver see ground-truth labels for
    # task X?" at the point where a template is about to inline raw
    # ``batch_results`` dicts into an evolver/planner prompt.  Reuse
    # these wherever ``success``/``score``/``detail``/``feedback`` would
    # otherwise leak from the harness's live results.

    _LABEL_FIELDS = (
        "success", "score", "detail", "feedback", "feedback_detail",
    )

    def is_revealed(self, task_id: str) -> bool:
        """Whether the evolver may see ground-truth labels for ``task_id``.

        - ``temporal_reveal=True`` overrides ``trajectory_only`` per-task:
          reveal iff the task is in ``_revealed_ids``.
        - ``trajectory_only=True`` without ``temporal_reveal`` → never.
        - Both off → always reveal (legacy behaviour).
        """
        if self.temporal_reveal:
            return task_id in self._revealed_ids
        if self.trajectory_only:
            return False
        return True

    def filter_batch_for_evolver(
        self, batch_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a shallow-copied batch with label fields stripped for
        any task that's not yet revealed.

        Unrevealed tasks still carry task_id, instance_id, turns,
        conversation, tool_timings, cut_off status — i.e. *behaviour*
        stays visible, only the *evaluation* is hidden.
        """
        out: list[dict[str, Any]] = []
        for r in batch_results:
            tid = r.get("instance_id") or r.get("task_id") or ""
            if self.is_revealed(tid):
                out.append(r)
                continue
            scrubbed = dict(r)
            for k in self._LABEL_FIELDS:
                scrubbed.pop(k, None)
            out.append(scrubbed)
        return out

    def update_reveal_state(
        self,
        cycle: int,
        watermark: datetime | None,
    ) -> list[dict[str, Any]]:
        """Scan the archive for tasks whose resolution date ≤ watermark
        and have not yet been surfaced.  Append them to
        ``revealed_supplement.jsonl`` and return the list for logging.

        Returns an empty list when ``temporal_reveal=False``, no archive,
        or no tasks crossed the threshold.
        """
        if not self.temporal_reveal or watermark is None:
            return []
        if not self._archive_path.exists():
            return []

        newly: list[dict[str, Any]] = []
        with open(self._archive_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                tid = entry.get("task_id")
                if not isinstance(tid, str) or tid in self._revealed_ids:
                    continue
                resolved = _parse_iso_tolerant(entry.get("resolution_date"))
                if resolved is None:
                    continue
                # Normalize watermark tz for comparison.
                wm = watermark if watermark.tzinfo else watermark.replace(tzinfo=timezone.utc)
                if resolved > wm:
                    continue
                # This task just became revealable.  Record in supplement.
                newly.append({
                    "task_id": tid,
                    "creation_date": _parse_iso_tolerant(entry.get("creation_date")),
                    "resolution_date": resolved,
                    "success": entry.get("success"),
                    "score": entry.get("score"),
                    "feedback_detail": entry.get("feedback_detail"),
                    "feedback": entry.get("feedback"),
                })
                self._revealed_ids.add(tid)

        if newly:
            with open(self._supplement_path, "a") as g:
                for r in newly:
                    g.write(json.dumps({
                        "task_id": r["task_id"],
                        "revealed_at_cycle": cycle,
                        "resolution_date": r["resolution_date"].isoformat()
                                          if r["resolution_date"] else None,
                        "success": r["success"],
                        "score": r["score"],
                        "feedback_detail": r["feedback_detail"],
                        "feedback": r["feedback"],
                    }, default=str) + "\n")

        total_past, total_revealed = self._count_past_and_revealed()
        logger.info("\n%s", render_reveal_update(
            cycle=cycle,
            watermark=watermark,
            in_batch_revealed=list(self._last_in_batch_reveals),
            newly_revealed_past=newly,
            total_revealed=total_revealed,
            total_past=total_past,
        ))
        return newly

    def _count_past_and_revealed(self) -> tuple[int, int]:
        """Return (total_past_tasks_seen, total_revealed)."""
        if not self._archive_path.exists():
            return 0, len(self._revealed_ids)
        n = 0
        with open(self._archive_path) as f:
            for line in f:
                if line.strip():
                    n += 1
        return n, len(self._revealed_ids)

    def get_recent_logs(self, n_batches: int = 3) -> list[dict[str, Any]]:
        """Read the most recent N batches of observations.

        In ``temporal_reveal`` mode, also overlay ``revealed_supplement.jsonl``
        so tasks whose labels were revealed in a later cycle (after the
        batch JSONL was first written) carry their feedback fields when
        the evolver sees them.
        """
        batch_files = sorted(self.observations_dir.glob("batch_*.jsonl"))
        recent_files = batch_files[-n_batches:]
        records: list[dict[str, Any]] = []
        for bf in recent_files:
            with open(bf) as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))

        if self.temporal_reveal and self._supplement_path.exists():
            # Build task_id → supplement entry map.
            by_id: dict[str, dict[str, Any]] = {}
            with open(self._supplement_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        s = json.loads(line)
                    except Exception:
                        continue
                    tid = s.get("task_id")
                    if isinstance(tid, str):
                        by_id[tid] = s
            # Overlay: for each record in the recent window whose task_id
            # has a supplement, attach the revealed feedback fields.
            for rec in records:
                tid = rec.get("task_id")
                if not isinstance(tid, str) or tid not in by_id:
                    continue
                s = by_id[tid]
                rec["success"] = s.get("success")
                rec["score"] = s.get("score")
                rec["feedback_detail"] = s.get("feedback_detail")
                rec["feedback"] = s.get("feedback")
        return records

    def get_summary_stats(self) -> dict[str, Any]:
        """Compute aggregate stats across all observations."""
        all_records = self.get_recent_logs(n_batches=9999)
        if not all_records:
            return {"total": 0, "success_rate": 0.0, "avg_score": 0.0}
        successes = sum(1 for r in all_records if r.get("success"))
        scores = [r.get("score", 0.0) for r in all_records]
        return {
            "total": len(all_records),
            "success_rate": successes / len(all_records),
            "avg_score": sum(scores) / len(scores),
        }

    def _next_batch_id(self) -> int:
        existing = list(self.observations_dir.glob("batch_*.jsonl"))
        if not existing:
            return 1
        ids = []
        for f in existing:
            try:
                ids.append(int(f.stem.split("_")[1]))
            except (IndexError, ValueError):
                continue
        return max(ids, default=0) + 1
