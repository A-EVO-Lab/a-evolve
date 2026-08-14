#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Run all 5 benchmarks:
#   1. CL-bench (background) + Terminal (foreground, wait)
#   2. Terminal done → SWE + WebArena (parallel, wait both)
#   3. Tau-bench (last)

echo "=== Starting CL-bench + Terminal in parallel ==="

bash "$SCRIPT_DIR/run_cl_evolve.sh" &
CL_PID=$!

bash "$SCRIPT_DIR/run_terminal_evolve.sh"
echo "=== Terminal done ==="

echo "=== Starting SWE + WebArena in parallel ==="

bash "$SCRIPT_DIR/run_swe_evolve.sh" &
SWE_PID=$!

bash "$SCRIPT_DIR/run_webarena_evolve.sh" &
WEB_PID=$!

wait $SWE_PID
echo "=== SWE done ==="
wait $WEB_PID
echo "=== WebArena done ==="

echo "=== Starting Tau-bench ==="
bash "$SCRIPT_DIR/run_tau_bench_evolve.sh"
echo "=== Tau-bench done ==="

wait $CL_PID
echo "=== CL-bench done ==="

echo "=== All benchmarks complete ==="
