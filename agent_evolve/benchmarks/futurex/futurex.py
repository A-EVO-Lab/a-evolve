"""FutureX benchmark adapter.

Implements the temporal-prediction benchmark.  The live search pipeline
is inside ``agent_evolve.agents.futurex.solver`` — this adapter only
loads task metadata, formats ``Task`` objects, and evaluates the
agent's final answer against ground truth.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from agent_evolve.benchmarks.base import BenchmarkAdapter
from agent_evolve.benchmarks.futurex.data_loader import FutureXDataLoader, FutureXTask
from agent_evolve.config import EvolveConfig
from agent_evolve.types import Feedback, Task, Trajectory

logger = logging.getLogger(__name__)


class FutureXBenchmarkTask:
    """FutureX-specific task wrapper with temporal constraints."""

    def __init__(self, futurex_task: FutureXTask):
        """Initialize from FutureXTask."""
        self.futurex_task = futurex_task

        # Create Task object compatible with framework
        self.task = Task(
            id=futurex_task.task_id,
            input=futurex_task.prompt,
            metadata={
                "title": futurex_task.title,
                "creation_date": futurex_task.creation_date.isoformat(),
                "resolution_date": futurex_task.resolution_date.isoformat(),
                "difficulty_level": futurex_task.difficulty_level,
                "domain": futurex_task.domain,
                "answer_format": futurex_task.answer_format,
                "requires_search": futurex_task.requires_search,
                "dataset_split": futurex_task.dataset_split,
                "expected_output": futurex_task.ground_truth
            }
        )

    def get_search_cutoff_date(self, safety_buffer_days: int = 1) -> datetime:
        """Get the cutoff date for time-bounded search."""
        return self.futurex_task.creation_date - timedelta(days=safety_buffer_days)

    def get_expected_answer_format(self) -> str:
        """Get expected answer format for validation."""
        return self.futurex_task.answer_format

    def validate_answer_format(self, answer: str) -> bool:
        """Validate answer follows expected format."""
        if not answer:
            return False

        answer = answer.strip()

        # Check for boxed format
        if self.futurex_task.answer_format == "boxed":
            return "\\boxed{" in answer or "boxed{" in answer

        # Other formats are more flexible
        return True

    def extract_boxed_answer(self, response: str) -> Optional[str]:
        """Extract answer from boxed format."""
        if not response:
            return None

        # Look for \\boxed{...} or boxed{...}
        patterns = [
            r'\\boxed\{([^}]+)\}',
            r'boxed\{([^}]+)\}'
        ]

        for pattern in patterns:
            matches = re.findall(pattern, response)
            if matches:
                return matches[-1].strip()  # Take the last match

        return None


class FutureXBenchmark(BenchmarkAdapter):
    """FutureX temporal prediction benchmark adapter."""

    def __init__(self, config: EvolveConfig):
        """Initialize FutureX benchmark."""
        self.config = config
        self.name = "futurex"
        self.data_loader = FutureXDataLoader()

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        """Return a batch of tasks from the FutureX benchmark."""

        # Map framework split to FutureX split
        futurex_split = "past" if split in ["train", "holdout", "test"] else "online"

        # Extract parameters from config
        domain = self.config.extra.get("futurex_domain")
        difficulty = self.config.extra.get("futurex_difficulty")

        # Use provided limit, but also respect config limit if smaller
        config_limit = self.config.extra.get("futurex_limit")
        if config_limit is not None:
            limit = min(limit, config_limit)

        task_ids = self.config.extra.get("futurex_task_ids")

        logger.info(f"Loading FutureX tasks: split={futurex_split}, domain={domain}, difficulty={difficulty}, limit={limit}")

        # Load tasks from data loader
        futurex_tasks = self.data_loader.load_tasks_for_evaluation(
            split=futurex_split,
            domain=domain,
            difficulty=difficulty,
            limit=None if task_ids else limit,
            sort_by_date=True  # Critical for temporal consistency
        )

        if task_ids:
            allowed = set(task_ids)
            # Each HF ID may have multiple weekly instances. Pick the latest
            # instance per ID (highest end_time) to match the live submission.
            by_hf: dict[str, list] = {}
            for t in futurex_tasks:
                if t.hf_id in allowed:
                    by_hf.setdefault(t.hf_id, []).append(t)
            futurex_tasks = [
                max(group, key=lambda t: t.resolution_date)
                for group in by_hf.values()
            ]
            futurex_tasks.sort(key=lambda t: t.creation_date)
            logger.info(f"Filtered to {len(futurex_tasks)} tasks by futurex_task_ids")

        # Convert to Task objects
        tasks = []
        for futurex_task in futurex_tasks:
            wrapper = FutureXBenchmarkTask(futurex_task)
            tasks.append(wrapper.task)

        logger.info(f"Loaded {len(tasks)} FutureX tasks")
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate a trajectory against the benchmark's ground truth."""

        # Get expected output from task metadata
        expected_output = task.metadata.get("expected_output", [])
        answer_format = task.metadata.get("answer_format", "unknown")
        domain = task.metadata.get("domain", "Unknown")
        difficulty = task.metadata.get("difficulty_level", 2)

        # Extract answer from trajectory output
        extracted_answer = self._extract_answer(trajectory.output, answer_format)

        # Score the response
        score, details = self._score_response(
            expected_output, extracted_answer, trajectory.output, answer_format
        )

        # Create feedback with rich detail for evolver
        detail_text = self._create_evaluation_detail(
            task, trajectory, extracted_answer, expected_output, details
        )

        return Feedback(
            success=score > 0.0,
            score=score,
            detail=detail_text,
            raw={
                "extracted_answer": extracted_answer,
                "expected_answers": expected_output,
                "answer_format": answer_format,
                "domain": domain,
                "difficulty": difficulty,
                **details
            }
        )

    def _extract_answer(self, response: str, answer_format: str) -> Optional[str]:
        """Extract final answer from agent response."""

        # Try boxed format first
        if answer_format == "boxed":
            boxed_answer = self._extract_boxed_answer(response)
            if boxed_answer:
                return boxed_answer

        # Fallback: look for common answer patterns
        answer_patterns = [
            r'(?:answer|prediction|result):\s*([^\n]+)',
            r'(?:final answer|my prediction):\s*([^\n]+)',
            r'\b([A-Z])\b(?:\s*[:.]\s*)',  # Single letter answers
            r'\b(yes|no|true|false)\b',  # Binary answers
            r'\b(\d+(?:\.\d+)?)\b'  # Numeric answers
        ]

        response_lower = response.lower()
        for pattern in answer_patterns:
            matches = re.findall(pattern, response_lower, re.IGNORECASE)
            if matches:
                return matches[-1].strip()

        # If no pattern matches, return last non-empty line as fallback
        lines = [line.strip() for line in response.split('\n') if line.strip()]
        if lines:
            return lines[-1]

        return None

    def _extract_boxed_answer(self, response: str) -> Optional[str]:
        """Extract answer from boxed format."""
        if not response:
            return None

        # Look for \\boxed{...} or boxed{...}
        patterns = [
            r'\\boxed\{([^}]+)\}',
            r'boxed\{([^}]+)\}'
        ]

        for pattern in patterns:
            matches = re.findall(pattern, response)
            if matches:
                return matches[-1].strip()  # Take the last match

        return None

    def _score_response(
        self,
        expected_output: List[str],
        extracted_answer: Optional[str],
        full_response: str,
        answer_format: str
    ) -> Tuple[float, Dict[str, Any]]:
        """Score the response against ground truth."""

        details = {
            "exact_match": False,
            "partial_match": False,
            "format_penalty": 0.0,
            "reasoning": ""
        }

        if not extracted_answer:
            details["reasoning"] = "No answer could be extracted"
            return 0.0, details

        if not expected_output:
            details["reasoning"] = "No ground truth available"
            return 0.0, details

        # Normalize answers for comparison
        extracted_normalized = self._normalize_answer(extracted_answer)
        expected_normalized = [self._normalize_answer(ans) for ans in expected_output]

        # Check exact match
        if extracted_normalized in expected_normalized:
            details["exact_match"] = True
            details["reasoning"] = "Exact match found"
            score = 1.0
        else:
            # Check partial match for multi-answer tasks
            if len(expected_output) > 1:
                matches = sum(1 for exp in expected_normalized
                            if self._is_partial_match(extracted_normalized, exp))
                if matches > 0:
                    details["partial_match"] = True
                    score = matches / len(expected_output)
                    details["reasoning"] = f"Partial match: {matches}/{len(expected_output)}"
                else:
                    score = 0.0
                    details["reasoning"] = "No matches found"
            else:
                # Single answer: check similarity
                similarity = self._compute_similarity(extracted_normalized, expected_normalized[0])
                if similarity > 0.8:  # High similarity threshold
                    score = similarity
                    details["reasoning"] = f"High similarity match: {similarity:.2f}"
                else:
                    score = 0.0
                    details["reasoning"] = f"Low similarity: {similarity:.2f}"

        # Apply format penalty if answer doesn't follow expected format
        if not self._validate_answer_format(full_response, answer_format):
            format_penalty = 0.1  # 10% penalty
            score = max(0.0, score - format_penalty)
            details["format_penalty"] = format_penalty
            details["reasoning"] += f" (format penalty: -{format_penalty})"

        return score, details

    def _validate_answer_format(self, response: str, answer_format: str) -> bool:
        """Validate answer follows expected format."""
        if not response:
            return False

        response = response.strip()

        # Check for boxed format
        if answer_format == "boxed":
            return "\\boxed{" in response or "boxed{" in response

        # Other formats are more flexible
        return True

    def _create_evaluation_detail(
        self,
        task: Task,
        trajectory: Trajectory,
        extracted_answer: Optional[str],
        expected_output: List[str],
        details: Dict[str, Any]
    ) -> str:
        """Create detailed evaluation text for the evolver."""

        lines = [
            f"FutureX Task Evaluation: {task.id}",
            f"Domain: {task.metadata.get('domain', 'Unknown')}",
            f"Difficulty: {task.metadata.get('difficulty_level', 'Unknown')}/4",
            f"Answer Format: {task.metadata.get('answer_format', 'unknown')}",
            "",
            f"Expected: {expected_output}",
            f"Extracted: {extracted_answer or 'None'}",
            f"Score: {details.get('score', 0.0):.2f}",
            "",
            f"Reasoning: {details.get('reasoning', 'No reasoning provided')}",
        ]

        if details.get("format_penalty", 0) > 0:
            lines.append(f"Format penalty applied: -{details['format_penalty']:.2f}")

        if task.metadata.get("requires_search", False):
            lines.append("Task required web search capability")

        return "\n".join(lines)

    def _normalize_answer(self, answer: str) -> str:
        """Normalize answer for comparison."""
        if not answer:
            return ""

        # Basic normalization
        normalized = answer.strip().lower()

        # Remove common prefixes/suffixes
        prefixes = ["the ", "a ", "an "]
        suffixes = [" inc", " corp", " ltd", " llc"]

        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]

        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]

        # Remove punctuation except for important cases
        normalized = re.sub(r'[^\w\s.-]', '', normalized)

        # Collapse whitespace
        normalized = ' '.join(normalized.split())

        return normalized

    def _is_partial_match(self, extracted: str, expected: str) -> bool:
        """Check if extracted answer partially matches expected."""
        if not extracted or not expected:
            return False

        # Simple substring matching
        return extracted in expected or expected in extracted

    def _compute_similarity(self, extracted: str, expected: str) -> float:
        """Compute similarity between two strings."""
        if not extracted or not expected:
            return 0.0

        # Simple Jaccard similarity on words
        extracted_words = set(extracted.split())
        expected_words = set(expected.split())

        if not extracted_words and not expected_words:
            return 1.0

        intersection = extracted_words.intersection(expected_words)
        union = extracted_words.union(expected_words)

        if not union:
            return 0.0

        return len(intersection) / len(union)

    def get_benchmark_stats(self) -> Dict[str, Any]:
        """Get comprehensive benchmark statistics."""
        stats = self.data_loader.get_dataset_stats()
        stats["benchmark_name"] = self.name
        stats["temporal_constraints"] = {
            "uses_time_bounded_search": True,
            "prevents_label_leakage": True,
            "default_safety_buffer_days": 1,
        }
        return stats


def create_futurex_benchmark(config: EvolveConfig) -> FutureXBenchmark:
    """Convenience factory."""
    return FutureXBenchmark(config)