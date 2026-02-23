#!/usr/bin/env bash
set -euo pipefail
# ──────────────────────────────────────────────────────────────────────
# AIOps Harness — Filmed Demo
#
# Usage:
#   ./scripts/demo.sh                    # full demo (inject + benchmark + results)
#   ./scripts/demo.sh --results-only     # skip injection, show latest results
# ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

CONSOLE_URL="$(oc whoami --show-console 2>/dev/null || echo 'https://console-openshift-console.apps.example.com')"

# Prometheus CPU query — shows per-container CPU rate for reviews-v2
CPU_QUERY='rate(container_cpu_usage_seconds_total{namespace="bookinfo",pod=~"reviews-v2.*",container!=""}[1m])'
CPU_QUERY_ENCODED="$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$CPU_QUERY")"
METRICS_URL="${CONSOLE_URL}/monitoring/query-browser?query0=${CPU_QUERY_ENCODED}"

# ─── Formatting ──────────────────────────────────────────────────────
BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
RESET="\033[0m"

section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}${CYAN}  $1${RESET}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
}

pause() {
    echo ""
    echo -e "${DIM}  [press enter]${RESET}"
    read -r
}

run_cmd() {
    echo -e "${GREEN}  \$ ${BOLD}$1${RESET}"
    echo ""
    eval "$1" 2>&1 | sed 's/^/    /'
    echo ""
}

open_browser() {
    open -a "Google Chrome" "$1" 2>/dev/null \
        || open "$1" 2>/dev/null \
        || xdg-open "$1" 2>/dev/null \
        || true
}

# ─── Parse args ──────────────────────────────────────────────────────
RESULTS_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --results-only) RESULTS_ONLY=true ;;
    esac
done

# ─────────────────────────────────────────────────────────────────────
# 1. Cluster & workloads
# ─────────────────────────────────────────────────────────────────────

section "Cluster Overview"

run_cmd "oc version"
pause

run_cmd "oc get nodes -l nvidia.com/gpu.present=true -o custom-columns='NODE:.metadata.name,GPUS:.status.capacity.nvidia\.com/gpu,ARCH:.status.nodeInfo.architecture' 2>/dev/null || oc get nodes -o wide"
pause

section "Bookinfo — System Under Test"

run_cmd "oc get pods -n bookinfo"
pause

section "LLM Serving"

run_cmd "oc get pods -n llm-serving -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,NODE:.spec.nodeName'"
pause

run_cmd "oc get routes -n llm-serving -o custom-columns='NAME:.metadata.name,HOST:.spec.host' 2>/dev/null || echo 'No routes found'"
pause

if [ "$RESULTS_ONLY" = true ]; then
    section "Results (existing benchmark)"
else

# ─────────────────────────────────────────────────────────────────────
# 2. Prometheus baseline — show flat CPU before injection
# ─────────────────────────────────────────────────────────────────────

section "Prometheus — CPU Baseline"

open_browser "$METRICS_URL"
# Give the browser a moment to load and render the graph
sleep 5
pause

# ─────────────────────────────────────────────────────────────────────
# 3. Fault injection + benchmark
# ─────────────────────────────────────────────────────────────────────

section "Fault Injection & Multi-Model Benchmark"

echo -e "${GREEN}  \$ ${BOLD}python3 scripts/local_benchmark.py${RESET}"
echo ""
python3 scripts/local_benchmark.py 2>&1 | sed 's/^/    /'
echo ""
pause

# ─────────────────────────────────────────────────────────────────────
# 4. Prometheus — show CPU spike from the injection
# ─────────────────────────────────────────────────────────────────────

section "Prometheus — CPU Saturation"

# Reopen the same metrics page; the graph now shows the spike
open_browser "$METRICS_URL"
sleep 5
pause

fi  # end of RESULTS_ONLY check

# ─────────────────────────────────────────────────────────────────────
# 5. Benchmark results
# ─────────────────────────────────────────────────────────────────────

section "Benchmark Results"

echo -e "${GREEN}  \$ ${BOLD}python3 scripts/show_results.py${RESET}"
echo ""
python3 scripts/show_results.py
echo ""
pause

section "Done"
