#!/usr/bin/env bash
set -euo pipefail

# Deploy all AIOps Harness Demo components
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
MANIFESTS="$ROOT_DIR/manifests"

echo "=== AIOps Harness Demo — Deploy All ==="
echo ""

# 1. Create namespaces
echo "Step 1: Creating namespaces..."
oc apply -f "$MANIFESTS/00-namespaces.yaml"
echo ""

# 2. Deploy RBAC
echo "Step 2: Deploying RBAC..."
oc apply -f "$MANIFESTS/50-harness/rbac.yaml"
echo ""

# 2b. Deploy vLLM model serving (requires HF_TOKEN secret and GPU node)
echo "Step 2b: Deploying vLLM model serving..."
if ! oc get secret llm-d-hf-token -n llm-serving > /dev/null 2>&1; then
    echo "  WARNING: HuggingFace token secret 'llm-d-hf-token' not found in llm-serving namespace."
    echo "  Create it with: oc create secret generic llm-d-hf-token --from-literal=HF_TOKEN=<your-token> -n llm-serving"
    echo "  Skipping vLLM deployment."
else
    # Create ServiceAccount for vLLM
    oc create serviceaccount granite-sa -n llm-serving 2>/dev/null || true
    oc apply -f "$MANIFESTS/30-llama-stack/vllm-serving.yaml"
    echo "  vLLM deployment applied. Model download may take several minutes on first run."
fi
echo ""

# 3. Deploy Bookinfo
echo "Step 3: Deploying Bookinfo application..."
oc apply -f "$MANIFESTS/20-bookinfo/bookinfo.yaml"
echo ""

# 4. Deploy traffic generator
echo "Step 4: Deploying traffic generator..."
oc apply -f "$MANIFESTS/20-bookinfo/traffic-generator.yaml"
echo ""

# 5. Deploy harness ConfigMaps
echo "Step 5: Deploying harness manifests (ConfigMaps)..."
oc apply -f "$MANIFESTS/50-harness/configmap-manifests.yaml"
echo ""

# 6. Deploy agent config
echo "Step 6: Deploying agent configuration..."
oc apply -f "$MANIFESTS/30-llama-stack/agent-config.yaml"
echo ""

# 7. Build and deploy tools server
echo "Step 7: Building and deploying tools server..."
# Use OpenShift BuildConfig to build the image in-cluster
if ! oc get buildconfig aiops-tools-server -n aiops-harness > /dev/null 2>&1; then
    echo "  Creating BuildConfig for tools server..."
    oc new-build --name=aiops-tools-server \
        --binary=true \
        --strategy=docker \
        --to=aiops-tools-server:latest \
        -n aiops-harness 2>/dev/null || true
fi

echo "  Starting build..."
oc start-build aiops-tools-server \
    --from-dir="$ROOT_DIR/tools" \
    --follow \
    -n aiops-harness

# Deploy the tools server (deployment + service from harness-runner-job.yaml — only tools server part)
# Extract just the tools server deployment and service
echo "  Deploying tools server..."
oc apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aiops-tools-server
  namespace: aiops-harness
  labels:
    app: aiops-tools-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: aiops-tools-server
  template:
    metadata:
      labels:
        app: aiops-tools-server
    spec:
      serviceAccountName: aiops-tools-server
      containers:
        - name: tools-server
          image: image-registry.openshift-image-registry.svc:5000/aiops-harness/aiops-tools-server:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 8000
              name: http
          env:
            - name: THANOS_QUERIER_URL
              value: "https://thanos-querier.openshift-monitoring.svc:9091"
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: aiops-tools-server
  namespace: aiops-harness
  labels:
    app: aiops-tools-server
spec:
  ports:
    - port: 8000
      targetPort: 8000
      name: http
  selector:
    app: aiops-tools-server
EOF
echo ""

# 8. Build harness runner image
echo "Step 8: Building harness runner image..."
if ! oc get buildconfig aiops-harness-runner -n aiops-harness > /dev/null 2>&1; then
    echo "  Creating BuildConfig for harness runner..."
    oc new-build --name=aiops-harness-runner \
        --binary=true \
        --strategy=docker \
        --to=aiops-harness-runner:latest \
        -n aiops-harness 2>/dev/null || true
fi

echo "  Starting build..."
oc start-build aiops-harness-runner \
    --from-dir="$ROOT_DIR/harness" \
    --follow \
    -n aiops-harness
echo ""

# 9. Wait for readiness
echo "Step 9: Waiting for deployments to be ready..."
echo "  Waiting for Bookinfo pods..."
oc rollout status deployment/productpage-v1 -n bookinfo --timeout=120s 2>/dev/null || echo "  (productpage still rolling out)"
oc rollout status deployment/details-v1 -n bookinfo --timeout=120s 2>/dev/null || echo "  (details still rolling out)"
oc rollout status deployment/reviews-v1 -n bookinfo --timeout=120s 2>/dev/null || echo "  (reviews-v1 still rolling out)"
oc rollout status deployment/reviews-v2 -n bookinfo --timeout=120s 2>/dev/null || echo "  (reviews-v2 still rolling out)"
oc rollout status deployment/reviews-v3 -n bookinfo --timeout=120s 2>/dev/null || echo "  (reviews-v3 still rolling out)"
oc rollout status deployment/ratings-v1 -n bookinfo --timeout=120s 2>/dev/null || echo "  (ratings still rolling out)"

echo "  Waiting for tools server..."
oc rollout status deployment/aiops-tools-server -n aiops-harness --timeout=120s 2>/dev/null || echo "  (tools server still rolling out)"

echo "  Checking vLLM model server..."
if oc get deployment granite-4-server -n llm-serving > /dev/null 2>&1; then
    oc rollout status deployment/granite-4-server -n llm-serving --timeout=600s 2>/dev/null || echo "  (vLLM still starting — model download can take several minutes)"
else
    echo "  (vLLM not deployed — see Step 2b output)"
fi

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Bookinfo route:"
oc get route productpage -n bookinfo -o jsonpath='  https://{.spec.host}{"\n"}' 2>/dev/null || echo "  (no route created)"
echo ""
echo "Next steps:"
echo "  ./scripts/20_run_harness_cpu.sh       # Run CPU saturation scenario"
echo "  ./scripts/21_run_harness_crashloop.sh  # Run CrashLoopBackOff scenario"
