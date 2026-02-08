#!/usr/bin/env bash
set -euo pipefail

# Run the CPU saturation harness scenario
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

NAMESPACE="aiops-harness"
JOB_NAME="harness-cpu-$(date +%Y%m%d%H%M%S)"
CONFIGMAP="harness-manifest-cpu"

echo "=== AIOps Harness — CPU Saturation Scenario ==="
echo ""

# Check prerequisites
echo "Checking prerequisites..."
oc get deployment aiops-tools-server -n "$NAMESPACE" > /dev/null 2>&1 || {
    echo "ERROR: Tools server not deployed. Run ./scripts/10_deploy_all.sh first."
    exit 1
}
oc get deployment reviews-v2 -n bookinfo > /dev/null 2>&1 || {
    echo "ERROR: Bookinfo not deployed. Run ./scripts/10_deploy_all.sh first."
    exit 1
}

# Delete any previous job with same base name
oc delete job -l scenario=cpu-saturation -n "$NAMESPACE" --ignore-not-found 2>/dev/null

echo "Creating harness job: $JOB_NAME"
echo ""

# Create the job
oc apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: aiops-harness-runner
    scenario: cpu-saturation
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 7200
  template:
    metadata:
      labels:
        app: aiops-harness-runner
        scenario: cpu-saturation
    spec:
      serviceAccountName: aiops-harness-runner
      restartPolicy: Never
      tolerations:
        - key: nvidia.com/gpu
          operator: Equal
          value: "true"
          effect: NoSchedule
      containers:
        - name: harness-runner
          image: image-registry.openshift-image-registry.svc:5000/aiops-harness/aiops-harness-runner:latest
          imagePullPolicy: Always
          env:
            - name: HARNESS_MANIFEST
              value: /config/manifest.yaml
            - name: LLAMA_STACK_URL
              value: "http://granite-4-server.llm-serving.svc.cluster.local:8080"
            - name: TOOLS_SERVER_URL
              value: "http://aiops-tools-server.aiops-harness.svc:8000"
            - name: LLAMA_MODEL_ID
              value: "granite-4"
            - name: HARNESS_OUTPUT_DIR
              value: /outputs
            - name: BASELINE_WAIT_SECONDS
              value: "60"
            - name: INJECTION_WAIT_SECONDS
              value: "120"
            - name: AGENT_TIMEOUT_SECONDS
              value: "300"
          volumeMounts:
            - name: manifest
              mountPath: /config
              readOnly: true
            - name: outputs
              mountPath: /outputs
          resources:
            requests:
              cpu: 200m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
      volumes:
        - name: manifest
          configMap:
            name: ${CONFIGMAP}
        - name: outputs
          emptyDir: {}
EOF

echo ""
echo "Job created. Following logs..."
echo "(This will take several minutes: baseline + injection + evidence + agent invocation)"
echo ""

# Wait for pod to start, then follow logs
sleep 5
POD=$(oc get pods -n "$NAMESPACE" -l job-name="$JOB_NAME" --no-headers -o custom-columns=":metadata.name" 2>/dev/null | head -1)
if [ -n "$POD" ]; then
    oc logs -f "$POD" -n "$NAMESPACE" 2>/dev/null || true
fi

# Check job status
echo ""
STATUS=$(oc get job "$JOB_NAME" -n "$NAMESPACE" -o jsonpath='{.status.conditions[0].type}' 2>/dev/null || echo "Unknown")
echo "Job status: $STATUS"
echo ""
echo "To fetch artifacts: ./scripts/30_fetch_artifacts.sh $JOB_NAME"
