#!/usr/bin/env bash
set -euo pipefail

# Cleanup all AIOps Harness Demo resources
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
MANIFESTS="$ROOT_DIR/manifests"

echo "=== AIOps Harness Demo — Cleanup ==="
echo ""

read -p "This will delete all harness demo resources. Continue? (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""

# 1. Delete harness jobs
echo "Deleting harness jobs..."
oc delete jobs -l app=aiops-harness-runner -n aiops-harness --ignore-not-found 2>/dev/null || true

# 2. Delete tools server
echo "Deleting tools server..."
oc delete deployment aiops-tools-server -n aiops-harness --ignore-not-found 2>/dev/null || true
oc delete service aiops-tools-server -n aiops-harness --ignore-not-found 2>/dev/null || true

# 3. Delete build configs
echo "Deleting build configs..."
oc delete buildconfig aiops-tools-server -n aiops-harness --ignore-not-found 2>/dev/null || true
oc delete buildconfig aiops-harness-runner -n aiops-harness --ignore-not-found 2>/dev/null || true

# 4. Delete configmaps
echo "Deleting configmaps..."
oc delete -f "$MANIFESTS/50-harness/configmap-manifests.yaml" --ignore-not-found 2>/dev/null || true
oc delete -f "$MANIFESTS/30-llama-stack/agent-config.yaml" --ignore-not-found 2>/dev/null || true

# 5. Delete RBAC
echo "Deleting RBAC..."
oc delete -f "$MANIFESTS/50-harness/rbac.yaml" --ignore-not-found 2>/dev/null || true

# 6. Delete Bookinfo
echo "Deleting Bookinfo..."
oc delete -f "$MANIFESTS/20-bookinfo/traffic-generator.yaml" --ignore-not-found 2>/dev/null || true
oc delete -f "$MANIFESTS/20-bookinfo/bookinfo.yaml" --ignore-not-found 2>/dev/null || true

# 7. Delete vLLM serving
echo "Deleting vLLM model serving..."
oc delete -f "$MANIFESTS/30-llama-stack/vllm-serving.yaml" --ignore-not-found 2>/dev/null || true

# 8. Delete namespaces
echo "Deleting namespaces..."
oc delete namespace bookinfo --ignore-not-found 2>/dev/null || true
oc delete namespace aiops-harness --ignore-not-found 2>/dev/null || true
oc delete namespace llm-serving --ignore-not-found 2>/dev/null || true

# 9. Delete cluster-scoped resources
echo "Deleting cluster-scoped RBAC..."
oc delete clusterrolebinding aiops-tools-server-prometheus-reader --ignore-not-found 2>/dev/null || true
oc delete clusterrolebinding aiops-tools-server-cluster-monitoring-view --ignore-not-found 2>/dev/null || true
oc delete clusterrole aiops-prometheus-reader --ignore-not-found 2>/dev/null || true

echo ""
echo "=== Cleanup Complete ==="
echo ""
echo "Note: Local artifacts in ./artifacts/ were NOT deleted."
echo "To remove them: rm -rf $ROOT_DIR/artifacts/"
