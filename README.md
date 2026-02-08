# Red Hat AIOps Harness Demo (OpenShift + OpenShift AI + Llama Stack)

This repository demonstrates an **AIOps Test Harness** pattern for infrastructure observability and closed-loop operations on OpenShift.

## What this demo shows
1) Deploy a realistic reference workload (**Bookinfo**) on OpenShift.
2) Collect evidence using **Prometheus + OpenTelemetry**.
3) Run an **external AIOps harness** that injects controlled failures and captures evidence.
4) Invoke a **Llama Stack** agent to investigate using **tool-based retrieval** (no raw telemetry dump into the LLM).
5) Generate contract-compliant artifacts:
   - `run.json` (run metadata)
   - `truth.json` (ground truth)
   - `aiops_output.json` (agent output + tool call log)
   - `score.json` (rubric-based evaluation)

## Why an external harness?
The harness must be independent from the agentic system it evaluates to ensure:
- unbiased scoring (no self-grading),
- reproducibility (stable artifacts, stable scoring),
- safety controls (read-only by default; gated remediation),
- auditability (evidence pointers + tool-call trace),
- portability across different “brains.”

## Demo scenarios
- **Scenario A: CPU Saturation** — inject CPU pressure into `reviews-v2`
- **Scenario B: CrashLoopBackOff** — induce `CrashLoopBackOff` in `ratings-v1` via a bad config env var

## Prerequisites
- OpenShift cluster (targeting **4.21.x**; any compatible OpenShift should work)
- `oc` CLI access with cluster-admin (or equivalent permissions for namespaces/operators used)
- Prometheus available (OpenShift Monitoring or user-workload monitoring)
- GPU node with NVIDIA runtime class (`runtimeClassName: nvidia`)
- HuggingFace token (for model download) stored as secret `llm-d-hf-token` in `llm-serving` namespace

> Note: The demo uses **vLLM** to serve the **ibm-granite/granite-4.0-h-tiny** model directly via
> the OpenAI-compatible `/v1/chat/completions` API with tool calling (`--tool-call-parser hermes`).
> Log/tracing backends vary by cluster. The tools-server interface supports optional integrations.

## Quickstart (happy path)
```bash
# 1) login to your cluster
oc whoami

# 2) sanity check prerequisites
./scripts/00_prereqs_check.sh

# 3) deploy everything
./scripts/10_deploy_all.sh

# 4) run CPU harness
./scripts/20_run_harness_cpu.sh

# 5) run CrashLoopBackOff harness
./scripts/21_run_harness_crashloop.sh

# 6) fetch artifacts locally
./scripts/30_fetch_artifacts.sh
```

Artifacts will be downloaded into:

- `./artifacts/latest/` (or a timestamped run folder)

## What you’ll present in the demo
- Harness Manifest (declares the scenario, evidence contract, tool contract, scoring gates)
- Evidence capture (Prometheus + k8s events; optionally logs/traces)
- Agent investigation via Llama Stack (tool calls are logged)
- Scorecard (Detection/Correlation/RCA/Action Safety/Auditability)

## Key files
- Harness scenario manifests (ConfigMaps):
  - `manifests/50-harness/configmap-manifests.yaml`
- Harness runner:
  - `harness/runner/main.py` (orchestrator)
  - `harness/runner/inject.py` (fault injection)
  - `harness/runner/evidence.py` (evidence capture)
  - `harness/runner/score.py` (rubric scoring)
- Tools server:
  - `tools/otel_tools_server/main.py`
- vLLM model serving:
  - `manifests/30-llama-stack/vllm-serving.yaml`
- Demo script:
  - `docs/demo-script.md`

## Architecture overview
- **Evidence Plane**: Prometheus + Kubernetes API (metrics, events, pod logs)
- **AIOps Plane**: Granite 4 model via vLLM with OpenAI-compatible tool calling
- **Harness Plane**: Orchestrator (injection + evidence + scoring) running as a K8s Job
- **Tools Server**: FastAPI service exposing `getMetricHistory`, `getK8sEvents`, `searchLogs`
- **Policy Gate**: Read-only default; remediation mode requires approval

## Cleanup
```bash
./scripts/90_cleanup.sh
```

## License
Apache-2.0 (recommended for broad community collaboration)
