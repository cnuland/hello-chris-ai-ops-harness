#!/usr/bin/env bash
set -euo pipefail

# Prerequisite check for AIOps Harness Demo
# Verifies: oc CLI, cluster connectivity, required services

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== AIOps Harness Demo — Prerequisite Check ==="
echo ""

PASS=0
FAIL=0

check() {
    local desc="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        echo "  [OK]   $desc"
        ((PASS++))
    else
        echo "  [FAIL] $desc"
        ((FAIL++))
    fi
}

# 1. oc CLI
echo "Checking CLI tools..."
check "oc CLI available" command -v oc
check "kubectl available (or oc as alias)" bash -c "command -v kubectl || command -v oc"

# 2. Cluster connectivity
echo ""
echo "Checking cluster connectivity..."
check "Logged into OpenShift cluster" oc whoami
check "Can list namespaces" oc get namespaces --no-headers

# 3. Cluster version
echo ""
echo "Cluster info:"
echo "  Server: $(oc whoami --show-server 2>/dev/null || echo 'unknown')"
echo "  User:   $(oc whoami 2>/dev/null || echo 'unknown')"
OC_VERSION=$(oc version -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('openshiftVersion','unknown'))" 2>/dev/null || echo "unknown")
echo "  OCP Version: $OC_VERSION"

# 4. Prometheus / monitoring
echo ""
echo "Checking monitoring stack..."
check "Prometheus available" oc get prometheus k8s -n openshift-monitoring
check "Thanos Querier route exists" oc get route thanos-querier -n openshift-monitoring

# 5. vLLM / Model Serving
echo ""
echo "Checking model serving (vLLM)..."
check "llm-serving namespace exists" oc get namespace llm-serving
check "granite-4-server deployment exists" oc get deployment granite-4-server -n llm-serving
check "granite-4-server pod running" oc get pod -n llm-serving -l app=granite-4-server --field-selector=status.phase=Running --no-headers

# 6. Check for existing harness namespaces
echo ""
echo "Checking harness namespaces (may not exist yet)..."
if oc get namespace bookinfo > /dev/null 2>&1; then
    echo "  [INFO] bookinfo namespace already exists"
else
    echo "  [INFO] bookinfo namespace does not exist (will be created)"
fi
if oc get namespace aiops-harness > /dev/null 2>&1; then
    echo "  [INFO] aiops-harness namespace already exists"
else
    echo "  [INFO] aiops-harness namespace does not exist (will be created)"
fi

# Summary
echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "Some prerequisites are missing. Please resolve before proceeding."
    exit 1
else
    echo "All prerequisites satisfied. Ready to deploy."
    exit 0
fi
