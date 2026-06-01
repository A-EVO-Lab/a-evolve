"""
FutureX data loader with HuggingFace integration.

Loads and processes FutureX datasets with proper temporal handling and answer format validation.
"""

import logging
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import json
import re

from datasets import load_dataset
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FutureXTask(BaseModel):
    """Single FutureX task with metadata."""
    task_id: str
    hf_id: str = ""
    title: str
    prompt: str
    ground_truth: List[str]
    creation_date: datetime
    resolution_date: datetime
    difficulty_level: int
    domain: str
    answer_format: str = "unknown"  # boxed, binary, multiple_choice, numeric, free_form
    requires_search: bool = True
    dataset_split: str = "past"  # "past" or "online"



class FutureXDataLoader:
    """Loads and processes FutureX datasets."""

    # Domain classification keywords
    DOMAIN_KEYWORDS = {
        "Technology": [
            "ai", "artificial intelligence", "model", "gpt", "llm", "software", "app", "api",
            "github", "coding", "programming", "tech", "startup", "ipo", "release", "version",
            "apple", "google", "microsoft", "meta", "tesla", "nvidia", "amazon", "openai",
            "anthropic", "tesla", "spacex", "cryptocurrency", "bitcoin", "ethereum"
        ],
        "Finance": [
            "stock", "price", "market", "trading", "investment", "earnings", "revenue",
            "financial", "economy", "gdp", "inflation", "fed", "interest rate", "bank",
            "nasdaq", "s&p", "dow", "forex", "currency", "bond", "commodity"
        ],
        "Sports": [
            "game", "match", "tournament", "championship", "league", "team", "player",
            "score", "win", "football", "basketball", "soccer", "tennis", "golf",
            "olympics", "nfl", "nba", "mlb", "nhl", "fifa", "premier league"
        ],
        "Politics": [
            "election", "vote", "president", "government", "policy", "congress",
            "senate", "house", "political", "democrat", "republican", "poll",
            "campaign", "candidate", "legislation", "supreme court", "biden", "trump"
        ]
    }

    def __init__(self, data_dir: Optional[Path] = None):
        """Initialize FutureX data loader."""
        self.data_dir = data_dir or Path("data/futurex")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_datasets(
        self,
        use_local: bool = True,
        refresh: bool = False
    ) -> Tuple[List[FutureXTask], List[FutureXTask]]:
        """
        Load both FutureX datasets (Past and Online).

        Args:
            use_local: Use local cached data if available
            refresh: Force refresh from HuggingFace even if local data exists

        Returns:
            Tuple of (past_tasks, online_tasks)
        """
        past_file = self.data_dir / "futurex_past.parquet"
        online_file = self.data_dir / "futurex_online.parquet"

        # Load or download datasets
        if use_local and past_file.exists() and online_file.exists() and not refresh:
            logger.info("Loading FutureX datasets from local cache")
            past_df = pd.read_parquet(past_file)
            online_df = pd.read_parquet(online_file)
        else:
            logger.info("Downloading FutureX datasets from HuggingFace")
            past_df = self._download_dataset("futurex-ai/FutureX-Past", past_file)
            online_df = self._download_dataset("futurex-ai/FutureX-Online", online_file)

        # Process datasets
        past_tasks = self._process_dataframe(past_df, "past")
        online_tasks = self._process_dataframe(online_df, "online")

        logger.info(f"Loaded {len(past_tasks)} past tasks and {len(online_tasks)} online tasks")
        return past_tasks, online_tasks

    def _download_dataset(self, dataset_name: str, save_path: Path) -> pd.DataFrame:
        """Download dataset from HuggingFace and save locally."""
        try:
            dataset = load_dataset(dataset_name, split="train")
            df = dataset.to_pandas()
            df.to_parquet(save_path)
            logger.info(f"Downloaded and saved {dataset_name} to {save_path}")
            return df
        except Exception as e:
            logger.error(f"Failed to download {dataset_name}: {e}")
            raise

    def _process_dataframe(self, df: pd.DataFrame, split: str) -> List[FutureXTask]:
        """Process raw dataframe into FutureXTask objects."""
        tasks = []

        for idx, row in df.iterrows():
            try:
                task = self._process_row(row, split, idx)
                if task:
                    tasks.append(task)
            except Exception as e:
                logger.warning(f"Failed to process row {idx}: {e}")
                continue

        # Sort by creation date for temporal consistency
        tasks.sort(key=lambda t: t.creation_date)
        return tasks

    def _parse_ground_truth_simple(self, ground_truth: Any) -> List[str]:
        """Simple ground truth parsing that handles various formats."""
        # Handle list/tuple before scalar pd.isna (which raises ValueError on arrays).
        if isinstance(ground_truth, (list, tuple)):
            return [str(item).strip() for item in ground_truth if pd.notna(item)]
        if pd.isna(ground_truth):
            return []

        if isinstance(ground_truth, str):
            # Try to parse as JSON array
            if ground_truth.strip().startswith('['):
                try:
                    parsed = json.loads(ground_truth)
                    return [str(item).strip() for item in parsed if pd.notna(item)]
                except:
                    # Try fixing single quotes to double quotes for JSON
                    try:
                        fixed = ground_truth.replace("'", '"')
                        parsed = json.loads(fixed)
                        return [str(item).strip() for item in parsed if pd.notna(item)]
                    except:
                        pass
            # Single string answer
            return [ground_truth.strip()]

        if isinstance(ground_truth, list):
            result = []
            for item in ground_truth:
                if pd.notna(item):  # Skip NaN values
                    result.append(str(item).strip())
            return [s for s in result if s]

        # Handle numeric and other types by converting to string
        if pd.notna(ground_truth):
            return [str(ground_truth).strip()]

        return []

    def _process_row(self, row: pd.Series, split: str, idx: int) -> Optional[FutureXTask]:
        """Process single dataframe row into FutureXTask."""

        # Extract basic fields (online dataset uses 'en_title' instead of 'title')
        title = str(row.get("title", row.get("en_title", f"Task_{idx}"))).strip()
        prompt = str(row.get("prompt", "")).strip()

        if not title or not prompt:
            logger.debug("Skipping row %d: missing title or prompt", idx)
            return None

        # Parse ground truth with simple handling
        # Online split has no ground_truth column — that's expected, not an error
        ground_truth = self._parse_ground_truth_simple(row.get("ground_truth"))
        if not ground_truth:
            if split == "online":
                ground_truth = []  # Online tasks have no GT — allow them through
            else:
                logger.warning("Skipping row %d: invalid ground truth", idx)
                return None

        # Parse dates — the dataset uses 'end_time' for resolution date.
        # 'creation_date' column doesn't exist; estimate as end_time - 7 days.
        resolution_date = (self._parse_date(row.get("resolution_date"))
                           or self._parse_date(row.get("end_time")))
        creation_date = self._parse_date(row.get("creation_date"))

        if not creation_date:
            if resolution_date:
                creation_date = resolution_date - timedelta(days=7)
            else:
                creation_date = datetime(2026, 1, 8, tzinfo=timezone.utc)

        if not resolution_date:
            resolution_date = creation_date + timedelta(days=7)

        # Extract metadata
        difficulty_level = int(row.get("difficulty", row.get("level", 2)))  # Default to medium
        domain = self._classify_domain(title, prompt)
        answer_format = self._detect_answer_format(prompt, ground_truth)
        requires_search = self._detect_search_requirement(prompt)

        # Generate task ID
        task_id = f"futurex_{split}_{idx:04d}_{creation_date.strftime('%Y%m%d')}"
        hf_id = str(row.get("id", ""))

        return FutureXTask(
            task_id=task_id,
            hf_id=hf_id,
            title=title,
            prompt=prompt,
            ground_truth=ground_truth,
            creation_date=creation_date,
            resolution_date=resolution_date,
            difficulty_level=difficulty_level,
            domain=domain,
            answer_format=answer_format,
            requires_search=requires_search,
            dataset_split=split
        )


    def _parse_date(self, date_value: Any) -> Optional[datetime]:
        """Parse date from various formats with GMT+8 timezone handling."""
        if pd.isna(date_value):
            return None

        if isinstance(date_value, datetime):
            # Ensure timezone awareness (assume GMT+8 if naive)
            if date_value.tzinfo is None:
                return date_value.replace(tzinfo=timezone(timedelta(hours=8)))
            return date_value

        if isinstance(date_value, str):
            date_str = date_value.strip()
            if not date_str:
                return None

            # Common date patterns
            patterns = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%d/%m/%Y",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z"
            ]

            for pattern in patterns:
                try:
                    parsed = datetime.strptime(date_str, pattern)
                    # Assume GMT+8 if timezone naive
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
                    return parsed
                except:
                    continue

        return None

    def _classify_domain(self, title: str, prompt: str) -> str:
        """Classify task domain based on content."""
        text = (title + " " + prompt).lower()

        # Count keyword matches per domain
        domain_scores = {}
        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword.lower() in text)
            if score > 0:
                domain_scores[domain] = score

        if not domain_scores:
            return "Other"

        # Return domain with highest score
        return max(domain_scores, key=domain_scores.get)

    def _detect_answer_format(self, prompt: str, ground_truth: List[str]) -> str:
        """Detect answer format requirements."""

        # Check for boxed format in prompt
        if "\\boxed{" in prompt or "boxed{" in prompt:
            return "boxed"

        # Check ground truth patterns
        if not ground_truth:
            return "unknown"

        first_answer = ground_truth[0].strip()

        # Binary answers
        if first_answer.lower() in ["yes", "no", "true", "false", "a", "b"]:
            return "binary"

        # Numeric answers
        if re.match(r'^-?\d+(\.\d+)?$', first_answer):
            return "numeric"

        # Multiple choice (single letter)
        if len(first_answer) == 1 and first_answer.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            return "multiple_choice"

        # Multiple answers
        if len(ground_truth) > 1:
            return "multiple_choice"

        return "free_form"

    def _detect_search_requirement(self, prompt: str) -> bool:
        """Detect if task likely requires web search."""
        prompt_lower = prompt.lower()

        # Search indicators
        search_indicators = [
            "latest", "current", "recent", "today", "now", "this week", "this month",
            "official", "announcement", "news", "update", "price", "ranking",
            "who will", "what will", "when will", "predict", "forecast"
        ]

        # Non-search indicators (more self-contained)
        non_search_indicators = [
            "given the following", "based on the text", "according to",
            "mathematical", "calculate", "solve", "logic", "reasoning"
        ]

        search_count = sum(1 for indicator in search_indicators if indicator in prompt_lower)
        non_search_count = sum(1 for indicator in non_search_indicators if indicator in prompt_lower)

        # Default to requiring search unless clearly self-contained
        return search_count > non_search_count

    def load_tasks_for_evaluation(
        self,
        split: str = "past",
        domain: Optional[str] = None,
        difficulty: Optional[int | list[int]] = None,
        limit: Optional[int] = None,
        sort_by_date: bool = True
    ) -> List[FutureXTask]:
        """Load tasks with filtering for evaluation."""

        # Always refresh online data (weekly rolling dataset)
        past_tasks, online_tasks = self.load_datasets(refresh=(split == "online"))
        tasks = past_tasks if split == "past" else online_tasks

        # Apply filters
        if domain:
            tasks = [t for t in tasks if t.domain.lower() == domain.lower()]

        if difficulty is not None:
            if isinstance(difficulty, list):
                allowed = set(difficulty)
                tasks = [t for t in tasks if t.difficulty_level in allowed]
            else:
                tasks = [t for t in tasks if t.difficulty_level == difficulty]

        # Sort by date for temporal consistency
        if sort_by_date:
            tasks.sort(key=lambda t: t.creation_date)

        # Apply limit
        if limit:
            tasks = tasks[:limit]

        logger.info(f"Loaded {len(tasks)} tasks (split={split}, domain={domain}, difficulty={difficulty})")
        return tasks

    def get_dataset_stats(self) -> Dict[str, Any]:
        """Get comprehensive dataset statistics."""
        past_tasks, online_tasks = self.load_datasets()
        all_tasks = past_tasks + online_tasks

        stats = {
            "total_tasks": len(all_tasks),
            "past_tasks": len(past_tasks),
            "online_tasks": len(online_tasks),
            "date_range": {
                "earliest": min(t.creation_date for t in all_tasks).isoformat(),
                "latest": max(t.resolution_date for t in all_tasks).isoformat(),
                "span_days": (max(t.resolution_date for t in all_tasks) -
                            min(t.creation_date for t in all_tasks)).days
            },
            "domains": self._get_distribution([t.domain for t in all_tasks]),
            "difficulty_levels": self._get_distribution([str(t.difficulty_level) for t in all_tasks]),
            "answer_formats": self._get_distribution([t.answer_format for t in all_tasks]),
            "search_requirements": {
                "requires_search": sum(1 for t in all_tasks if t.requires_search),
                "no_search": sum(1 for t in all_tasks if not t.requires_search)
            }
        }

        return stats

    def _get_distribution(self, values: List[str]) -> Dict[str, int]:
        """Get distribution of categorical values."""
        from collections import Counter
        return dict(Counter(values))


# Utility functions
def load_futurex_tasks(
    split: str = "past",
    data_dir: Optional[Path] = None,
    **kwargs
) -> List[FutureXTask]:
    """Convenient function to load FutureX tasks."""
    loader = FutureXDataLoader(data_dir)
    return loader.load_tasks_for_evaluation(split=split, **kwargs)


def get_futurex_stats(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Convenient function to get FutureX dataset statistics."""
    loader = FutureXDataLoader(data_dir)
    return loader.get_dataset_stats()


# Example usage
if __name__ == "__main__":
    # Load and display dataset info
    loader = FutureXDataLoader()
    stats = loader.get_dataset_stats()

    print("FutureX Dataset Statistics:")
    print(f"Total tasks: {stats['total_tasks']}")
    print(f"Past tasks: {stats['past_tasks']}")
    print(f"Online tasks: {stats['online_tasks']}")
    print(f"Date range: {stats['date_range']['span_days']} days")
    print()

    print("Domain distribution:")
    for domain, count in stats['domains'].items():
        pct = count / stats['total_tasks'] * 100
        print(f"  {domain}: {count} ({pct:.1f}%)")
    print()

    print("Difficulty distribution:")
    for level, count in stats['difficulty_levels'].items():
        pct = count / stats['total_tasks'] * 100
        print(f"  Level {level}: {count} ({pct:.1f}%)")

    # Sample tasks
    print("\nSample tasks:")
    past_tasks = loader.load_tasks_for_evaluation(split="past", limit=3)
    for i, task in enumerate(past_tasks, 1):
        print(f"{i}. {task.title}")
        print(f"   Domain: {task.domain}, Difficulty: {task.difficulty_level}")
        print(f"   Created: {task.creation_date}")
        print(f"   Format: {task.answer_format}, Search: {task.requires_search}")
        print()