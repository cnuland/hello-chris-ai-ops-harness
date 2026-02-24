# AIOps Harness Demo — Video Recording Guide

Step-by-step walkthrough. Every command is copy-paste ready.
Total runtime: ~12-15 minutes on camera (assuming infrastructure is already deployed).

---

## Before You Hit Record

Make sure everything is deployed and healthy. Run these checks:

```bash
# Verify you're logged in
oc whoami

# Check all pods are running
oc get pods -n llm-serving       # granite-4-server, qwen3-coder-next should be Running
oc get pods -n llama-stack       # llama-stack-aiops should be Running
oc get pods -n bookinfo          # all 7 pods should be 1/1 Running
oc get pods -n aiops-harness     # aiops-tools-server should be 1/1 Running
oc get pods -n mlflow-aiops      # mlflow pod should be Running
oc get pods -n mlflow-harness    # mlflow pod should be Running

# Quick health check on the tools server
oc exec deploy/aiops-tools-server -n aiops-harness -- curl -s http://localhost:8000/healthz

# Quick health check on Llama Stack
oc exec deploy/llama-stack-aiops -n llama-stack -- curl -s http://localhost:8080/v1/models
```

If anything is missing, deploy with:
```bash
./scripts/10_deploy_all.sh
```

Clean up any leftover jobs from previous runs:
```bash
oc delete jobs -l app=aiops-harness-runner -n aiops-harness --ignore-not-found
```

---

## Part 1: Set the Scene (1-2 min)

Start recording. Open a terminal.

**Talk track:** "This is a demo of an external AIOps test harness running on OpenShift.
The key idea: the harness is independent from the AI system it evaluates. It controls
fault injection, evidence capture, and scoring. The AI model only sees evidence through
structured tool calls, and an external eval model fact-checks the results using only
the data the harness provides."

### Show what's running

```bash
# Show the cluster
oc whoami
oc whoami --show-server
```

```bash
# Show the key namespaces
oc get pods -n llm-serving -o wide
oc get pods -n llama-stack
oc get pods -n bookinfo
oc get pods -n aiops-harness
oc get pods -n mlflow-aiops
oc get pods -n mlflow-harness
```

**Talk track:** "We have six key components:
1. **vLLM** serving multiple models on H200 GPUs — Granite 4 and Qwen3-Coder-Next
2. **Llama Stack** wrapping vLLM to provide the agent runtime — ReAct loop, tool dispatch, session management
3. **Bookinfo** — the system under test, a microservices app with 6 services
4. **Tools Server** — a FastAPI gateway providing Prometheus metrics, K8s events, logs, and documentation search
5. **Harness Runner** — the orchestrator that runs as a Kubernetes Job
6. **MLFlow** — two instances tracking pipeline behavior and evaluation results separately"

### Show the Bookinfo app is live

```bash
# Get the route
oc get route productpage -n bookinfo -o jsonpath='{.spec.host}' && echo ""
```

Open the URL in a browser briefly to show it's a real running app.

```bash
# Show traffic is flowing (traffic generator is producing baseline metrics)
oc logs deploy/traffic-generator -n bookinfo --tail=5
```

---

## Part 2: Show the Architecture (1-2 min)

**Talk track:** "The architecture has three planes that are physically separated:

- The **Evidence Plane** — Prometheus, OpenTelemetry, K8s API. It records what happens.
- The **AIOps Plane** — Llama Stack agent backed by vLLM. It investigates incidents using tools. It cannot access ground truth or the harness.
- The **Harness Plane** — The orchestrator, scoring engine, and eval model. It's the only component with access to ground truth.

The eval model must exist **outside** the system being evaluated. It only sees the data the harness provides — the pipeline's output, tool call logs, and ground truth. It cannot query Prometheus or Kubernetes directly. This is what makes the evaluation honest."

### Show the agent configuration

```bash
# Show how the agent is configured
oc get configmap aiops-agent-config -n aiops-harness -o jsonpath='{.data.agent-config\.yaml}' | head -40
```

Point out on screen:
- `llama_stack_url` — the Llama Stack endpoint (agent runtime)
- `model_id` — which model runs inside the pipeline
- `tools_server_url` — where the agent's investigative tools live
- `eval_model_url` / `eval_model_id` — the external eval model (separate from the pipeline)
- `system_prompt` — instructions for the SRE agent

**Talk track:** "Notice the eval model is a completely separate endpoint. It's not
the same model that runs the investigation — it's an independent model that fact-checks
the investigation using only what the harness gives it."

---

## Part 3: Show the Harness Manifest and Tools (1-2 min)

**Talk track:** "Each scenario is defined declaratively in a HarnessManifest.
It specifies what to break, how to score, and what evidence to collect."

```bash
# Show the CrashLoopBackOff manifest
oc get configmap harness-manifest-crashloop -n aiops-harness -o jsonpath='{.data.manifest\.yaml}' | head -30
```

Point out on screen:
- `fault.type: crashloop_bad_config` — what we're injecting
- `fault.targetSelector.name: ratings-v1` — which service we're targeting
- `fault.parameters` — the bad env var that causes the crash

**Talk track:** "The harness knows the ground truth because it controls the injection.
The AI model does NOT know what was injected — it has to figure it out using tools."

### Show the tools contract

```bash
# Show the tools server endpoints
oc exec deploy/aiops-tools-server -n aiops-harness -- curl -s http://localhost:8000/openapi.json | python3 -m json.tool | head -40
```

Describe the tools:
- `getMetricHistory` — runs PromQL queries against Prometheus/Thanos
- `getK8sEvents` — fetches Kubernetes events (pod crashes, restarts, scheduling)
- `searchLogs` — searches pod logs for error patterns
- `searchDocumentation` — RAG search against OpenShift docs and SRE runbooks

**Talk track:** "The model decides what to query, the tools server executes it, and the results
come back as structured data. Every tool call is logged — this is the audit trail."

---

## Part 4: Run the Benchmark (4-5 min)

**Talk track:** "Now let's run the full benchmark. This injects a CPU saturation fault,
then has multiple models investigate the incident. Each model runs independently through
Llama Stack, and then the eval model scores each investigation."

```bash
python3 scripts/local_benchmark.py
```

The script will stream output in real time. Narrate each phase as it appears:

| Output | What to say |
|--------|-------------|
| `Injecting CPU stress sidecar...` | "Injecting a CPU saturation fault into reviews-v2 — a stress container that consumes 95% CPU" |
| `Waiting for fault propagation (120s)` | "Giving the fault time to manifest in Prometheus metrics and Kubernetes events" |
| `Running model: granite-4 (via Llama Stack)` | "Now Granite 4, a 1B-parameter model, is investigating through Llama Stack" |
| `Tool call: getMetricHistory(...)` | "The agent decided to query Prometheus for CPU metrics — this is a real tool call" |
| `Tool call: getK8sEvents(...)` | "Now checking Kubernetes events for pod health issues" |
| `Tool call: searchDocumentation(...)` | "The agent is searching documentation for the right PromQL metric name — RAG in action" |
| `Model complete: 0.81 (PASS)` | "Granite scored 0.81 with RAG augmentation — documentation search helped it find the right metrics" |
| `Running model: qwen3-coder-next (via Llama Stack)` | "Now Qwen3, an 80B model, gets its turn — same fault, same tools, independent investigation" |
| `Eval model scoring...` | "The external eval model is now fact-checking each investigation — using only the harness-provided data" |
| `Logging to MLFlow...` | "Results are being tracked in MLFlow for regression testing" |

While waiting, you can show the fault in action:

```bash
# In a second terminal — show CPU spike in the pod
oc top pods -n bookinfo
```

---

## Part 5: Show Results and MLFlow (3-4 min)

### Terminal results

```bash
python3 scripts/show_results.py
```

Walk through the output:
- **Model cards** — each model's composite score, PASS/FAIL, tool call count, investigation time
- **Score breakdown** — six dimensions: Detection, Correlation, RCA Detected (binary gate), RCA Eval (eval model score), Action Safety, Auditability
- **RCA Hypotheses** — what each model concluded as the root cause
- **Investigation Detail** — the actual tool calls each model made

**Talk track:** "RCA Detected is a binary gate — did the model name the right root cause?
RCA Eval is the heavy hitter at 50% of the total weight. The external eval model
scores the investigation quality: was the evidence real? Was the reasoning sound?
Was the remediation safe? A model can pass the gate but still fail overall if the
eval model determines the evidence was hallucinated."

### MLFlow dashboards

```bash
# Get the MLFlow routes
oc get routes -n mlflow-aiops -o jsonpath='{.items[0].spec.host}' && echo ""
oc get routes -n mlflow-harness -o jsonpath='{.items[0].spec.host}' && echo ""
```

Open both URLs in a browser:

**AIOps MLFlow** — "This tracks the pipeline's behavior: which model ran, what tool calls
it made, how long the investigation took, and the full RCA output."

**Harness MLFlow** — "This tracks the evaluation results: all six scoring dimensions,
the eval model's assessment, PASS/FAIL, and the weighted composite. These are
physically separated — the pipeline team sees their own metrics, the evaluation team
sees theirs."

**Talk track:** "MLFlow is the central tracking backbone. Every run produces records
here. You can compare models across runs, detect regressions when you change prompts
or configurations, and build a history of measured capability over time."

---

## Part 6: Walk the Artifacts (2 min)

```bash
# Show the latest benchmark artifacts
ls artifacts/benchmark-*/
```

Walk through key files:

```bash
# Ground truth — what the harness injected (never shown to the agent)
cat artifacts/benchmark-*/truth.json | python3 -m json.tool | head -10
```

```bash
# Agent output — what one model concluded (pick the best performer)
cat artifacts/benchmark-*/granite-ls/aiops_output.json | python3 -m json.tool | head -30
```

Point out:
- `rca_ranked` — the model's root cause hypotheses
- `tool_calls` — the complete audit trail of every tool invocation
- `evidence_links` — what evidence the model referenced

**Talk track:** "This is the contract. Four artifacts per run: `run.json` (what we ran),
`truth.json` (what actually happened), `aiops_output.json` (what the agent concluded),
`score.json` (how it did). These are immutable governance artifacts — any auditor can
reconstruct exactly what happened."

---

## Part 7: Key Takeaways (1 min)

**Talk track:**

"Four things to take away:

1. **The harness is external.** The AI model doesn't know it's being tested.
   It gets an incident trigger and tools — that's it.

2. **Tool-based retrieval, not telemetry dumping.** The model queries what it needs
   through structured APIs. Every call is logged. This keeps evidence auditable.

3. **The eval model is independent.** It exists outside the system being evaluated
   and only sees what the harness provides. It can't access the cluster directly.
   This is what makes the evaluation honest.

4. **Everything is tracked.** MLFlow captures every run, every score, every dimension.
   You can swap models, change prompts, update RAG content — and measure the impact
   with data, not guesswork.

That's the harness-first AIOps architecture."

---

## Optional: Show Scenario C (Distributed Cascading Failure)

If you have time, run the distributed scenario:

```bash
python3 scripts/distributed_benchmark.py
```

**Talk track:** "This injects TWO faults with a 60-second stagger — a CrashLoopBackOff
in ratings-v1 at T+0 and CPU saturation in reviews-v2 at T+60. The agent has to find
BOTH root causes and understand the temporal ordering. Finding both earns full credit,
finding one earns partial credit."

---

## After Recording: Cleanup

```bash
# Delete harness jobs
oc delete jobs -l app=aiops-harness-runner -n aiops-harness --ignore-not-found

# Full teardown (if needed)
./scripts/90_cleanup.sh
```

---

## Terminal Tips for Video

- Use a dark terminal theme with large font (16-18pt)
- Set `export PS1='\n\$ '` for a clean prompt
- Consider `export TERM=xterm-256color` for better colors
- If the log output scrolls too fast, you can re-read it afterward: `oc logs -n aiops-harness job/<job-name>`
- The benchmark run takes ~5-8 minutes total (injection + propagation + multiple models + eval scoring)
- Have a second terminal ready to show `oc top pods -n bookinfo` during injection
