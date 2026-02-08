# Claude Code Project Definition: Red Hat AIOps Harness Demo

## Objective
Build a reproducible demo of an AIOps test harness running on OpenShift that:
- deploys the Bookinfo reference app
- collects evidence via Prometheus + OpenTelemetry
- invokes a Llama Stack agent to investigate incidents using tool-based retrieval (no raw telemetry dump into the LLM)
- emits harness artifacts (run.json, truth.json, aiops_output.json, score.json) aligned to the harness contract
- presents results in a “demo-friendly” narrative and files

## Hard Requirements
1) Harness must be external to the agentic system:
   - harness orchestrator controls injection, evidence capture, scoring
   - Llama Stack agent only sees evidence through tools and timeboxing
2) Keep harnesses simple:
   - one hypothesis per harness
   - prefer multiple harnesses vs one complex harness
3) Produce contract-compliant output:
   - run.json, truth.json, score.json, aiops_output.json
   - include tool-call logs and evidence pointers
4) Target OpenShift 4.21.x in manifests and docs (do not claim “latest”; use “4.21.x target” language).

## Demo Scenarios
- Scenario A: CPU saturation against Bookinfo reviews-v2
- Scenario B: CrashLoopBackOff against Bookinfo ratings-v1 (bad config env var)

## Implementation Notes
- Provide k8s manifests in /manifests and runnable scripts in /scripts
- The harness runner can be a Python container executed as a Job
- Tools server can be a simple HTTP service (FastAPI) that provides:
  - getMetricHistory (Prometheus)
  - searchLogs (implementation placeholder or cluster logging if available)
  - getTraceWaterfall (Tempo/Jaeger if available)
  - getK8sEvents (Kubernetes API)
- Keep external dependencies minimal and well documented

## Deliverables
- A polished README with quickstart
- A live demo script in docs/demo-script.md (10–15 min)
- Troubleshooting guide and clear cleanup steps
