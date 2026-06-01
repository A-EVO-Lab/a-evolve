#!/usr/bin/env bash
set -euo pipefail

# Download Terminal-Bench 2.0 challenges from the pinned GitHub commit.
#
# Uses git sparse checkout to download only the challenges directory,
# not the entire inspect_evals repository.
#
# Usage:
#   bash agent_evolve/benchmarks/tb2/download_challenges.sh
#   bash agent_evolve/benchmarks/tb2/download_challenges.sh /custom/target/dir

TB2_REPO="https://github.com/UKGovernmentBEIS/inspect_evals.git"
TB2_COMMIT="6e30b2de72e98dd5cc342eb9ba545ae27d2f63d7"
TB2_SUBPATH="src/inspect_evals/terminal_bench_2/challenges"

# Target directory: argument or default (relative to this script)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="${1:-$SCRIPT_DIR/challenges}"

if [[ -d "$TARGET_DIR" ]] && [[ $(find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -type d | head -1) ]]; then
    COUNT=$(find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
    echo "Challenges directory already exists with $COUNT challenges: $TARGET_DIR"
    echo "To re-download, remove the directory first: rm -rf $TARGET_DIR"
    exit 0
fi

TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

echo "Downloading Terminal-Bench 2.0 challenges..."
echo "  Repo:   $TB2_REPO"
echo "  Commit: $TB2_COMMIT"
echo "  Target: $TARGET_DIR"
echo ""

# Sparse checkout: only fetch the challenges subdirectory
git clone --depth 1 --filter=blob:none --sparse \
    "$TB2_REPO" "$TMP_DIR/repo" 2>&1 | tail -1

cd "$TMP_DIR/repo"
git sparse-checkout set "$TB2_SUBPATH"
git checkout "$TB2_COMMIT" -- "$TB2_SUBPATH" 2>/dev/null

# Copy challenges to target
mkdir -p "$TARGET_DIR"
cp -r "$TMP_DIR/repo/$TB2_SUBPATH"/* "$TARGET_DIR/"

COUNT=$(find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
echo ""
echo "Done. Downloaded $COUNT challenges to $TARGET_DIR"
