#!/usr/bin/env bash
set -euo pipefail

# Fetch harness artifacts from a completed job
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

NAMESPACE="aiops-harness"
ARTIFACTS_DIR="$ROOT_DIR/artifacts"

echo "=== AIOps Harness — Fetch Artifacts ==="
echo ""

# Get the job name — either from arg or find the latest
if [ "${1:-}" ]; then
    JOB_NAME="$1"
else
    # Find the most recent harness job
    JOB_NAME=$(oc get jobs -n "$NAMESPACE" -l app=aiops-harness-runner \
        --sort-by=.metadata.creationTimestamp \
        -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || echo "")
    if [ -z "$JOB_NAME" ]; then
        echo "ERROR: No harness jobs found in $NAMESPACE namespace."
        echo "Usage: $0 [JOB_NAME]"
        exit 1
    fi
fi

echo "Fetching artifacts from job: $JOB_NAME"

# Find the pod for this job
POD=$(oc get pods -n "$NAMESPACE" -l job-name="$JOB_NAME" \
    --no-headers -o custom-columns=":metadata.name" 2>/dev/null | head -1)

if [ -z "$POD" ]; then
    echo "ERROR: No pod found for job $JOB_NAME"
    exit 1
fi

echo "Pod: $POD"
echo ""

# Create local artifacts directory
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOCAL_DIR="$ARTIFACTS_DIR/$JOB_NAME"
mkdir -p "$LOCAL_DIR"

# Save job logs first (needed for artifact extraction)
echo "Saving job logs..."
oc logs "$POD" -n "$NAMESPACE" > "$LOCAL_DIR/harness-runner.log" 2>/dev/null || echo "  (could not retrieve logs)"

# Try to copy artifacts from the pod's /outputs directory
echo ""
echo "Copying artifacts..."
FOUND_VIA_POD=false
for artifact in run.json truth.json aiops_output.json score.json; do
    REMOTE_PATH=$(oc exec "$POD" -n "$NAMESPACE" -- find /outputs -name "$artifact" -type f 2>/dev/null | head -1 || echo "")
    if [ -n "$REMOTE_PATH" ]; then
        oc cp "$NAMESPACE/$POD:$REMOTE_PATH" "$LOCAL_DIR/$artifact" 2>/dev/null
        echo "  $artifact -> $LOCAL_DIR/$artifact"
        FOUND_VIA_POD=true
    fi
done

# If artifacts not found in pod (emptyDir lost), extract from logs
if [ "$FOUND_VIA_POD" = false ] && [ -f "$LOCAL_DIR/harness-runner.log" ]; then
    echo "  Artifacts not found in pod filesystem, extracting from logs..."
    for artifact in run.json truth.json aiops_output.json score.json; do
        python3 -c "
import sys
in_block = False
lines = []
marker_start = '===ARTIFACT_START:${artifact}==='
marker_end = '===ARTIFACT_END:${artifact}==='
with open('$LOCAL_DIR/harness-runner.log') as f:
    for line in f:
        line = line.rstrip()
        if marker_start in line:
            in_block = True
            continue
        if marker_end in line:
            in_block = False
            continue
        if in_block:
            lines.append(line)
if lines:
    with open('$LOCAL_DIR/$artifact', 'w') as out:
        out.write('\n'.join(lines) + '\n')
    print(f'  $artifact -> $LOCAL_DIR/$artifact (from logs)')
else:
    print(f'  $artifact: not found in logs')
" 2>/dev/null
    done
fi

# Create symlink for latest
ln -sfn "$JOB_NAME" "$ARTIFACTS_DIR/latest"

echo ""
echo "=== Artifacts saved to: $LOCAL_DIR ==="
echo "Latest symlink: $ARTIFACTS_DIR/latest"
echo ""

# Print summary if score.json exists
if [ -f "$LOCAL_DIR/score.json" ]; then
    echo "--- Score Summary ---"
    python3 -c "
import json
with open('$LOCAL_DIR/score.json') as f:
    s = json.load(f)
print(f\"  Result:     {s.get('result', 'N/A')}\")
print(f\"  Composite:  {s.get('weighted_score', 'N/A')}\")
scores = s.get('category_scores', {})
for k, v in scores.items():
    print(f\"  {k:15s} {v}\")
" 2>/dev/null || echo "  (could not parse score.json)"
fi
