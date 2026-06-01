#!/usr/bin/env python3
"""Stub downloader for benchmark data.

This script documents the data sources for the three benchmarks used
by the framework. The actual download endpoints are public and listed
in `data/README.md`. The stub below is intentionally light: it prints
the source URL and target path so a reviewer can fetch the data
manually without committing the dataset to this repo.

Replace the print calls with real downloads (urllib, requests, git
clone, or huggingface_hub) for an automated setup.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SOURCES = {
    "polybench": {
        "url": "https://polymarket.com (public API)",
        "target": "polymarket_analysis.db",
        "instructions": (
            "PolyBench uses a SQLite snapshot of Polymarket markets "
            "resolving Feb 6 to 22, 2026. Build the DB by querying the "
            "Polymarket public API for markets in that window and "
            "saving each market's metadata, last-known prices, and "
            "resolved outcomes."
        ),
    },
    "ctf_dojo": {
        "url": "https://github.com/pwncollege/ctf-archive",
        "target": "ctf-archive/",
        "instructions": (
            "git clone https://github.com/pwncollege/ctf-archive into "
            "data/ctf-archive. Then produce data/ctf_archive.json "
            "with one entry per challenge containing category, year, "
            "event, files list, description, and flag SHA-256."
        ),
    },
    "futurex": {
        "url": "huggingface.co/datasets/futurex-ai/FutureX-Past + FutureX-Online",
        "target": "futurex/futurex_past.parquet (auto-created)",
        "instructions": (
            "No manual step required. The framework's data loader at "
            "agent_evolve/benchmarks/futurex/data_loader.py downloads "
            "the parquet files from HuggingFace on first use and "
            "caches them under data/futurex/. To pre-warm the cache, "
            "run: python -c \"from agent_evolve.benchmarks.futurex"
            ".data_loader import FutureXDataLoader; "
            "FutureXDataLoader().load_datasets()\""
        ),
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        required=True,
        choices=sorted(SOURCES.keys()) + ["all"],
        help="Which benchmark to fetch.",
    )
    args = parser.parse_args()

    targets = sorted(SOURCES.keys()) if args.benchmark == "all" else [args.benchmark]
    for key in targets:
        s = SOURCES[key]
        print(f"\n=== {key} ===")
        print(f"Source:       {s['url']}")
        print(f"Target path:  {ROOT / s['target']}")
        print(f"Instructions: {s['instructions']}")


if __name__ == "__main__":
    main()
