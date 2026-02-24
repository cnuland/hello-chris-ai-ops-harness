# Live Demo Script (10-15 minutes)

## Prerequisites (before the demo)
- OpenShift cluster logged in via `oc`
- vLLM serving models in `llm-serving` namespace (Granite 4 and/or Qwen3-Coder-Next)
- Llama Stack agent runtime in `llama-stack` namespace
- Bookinfo + tools server deployed via `./scripts/10_deploy_all.sh`
- MLFlow instances running in `mlflow-aiops` and `mlflow-harness` namespaces
- Verify with: `./scripts/00_prereqs_check.sh`

## 1. Context (1-2 min)

Set the stage:

> "We're demonstrating an external AIOps test harness. The key principle: the harness
> must be independent from the AI system it evaluates. This ensures unbiased scoring,
> reproducibility, and auditability. An external eval model fact-checks the results
> using only the data the harness provides — it cannot access the cluster directly."

Show the architecture:
- **Bookinfo** = System Under Test (microservices app)
- **vLLM** = Model serving (GPU inference for Granite 4, Qwen3-Coder-Next)
- **Llama Stack** = Agent runtime (ReAct loop, tool dispatch, session management)
- **Tools Server** = Evidence gateway (Prometheus, K8s events, logs, documentation RAG)
- **Harness Runner** = Orchestrator (inject -> capture -> invoke -> score)
- **Eval Model** = External fact-checker (separate from the pipeline, only sees harness data)
- **MLFlow** = Experiment tracking (two instances: pipeline behavior + evaluation results)

## 2. Show the Agent Config (1 min)

```bash
oc get configmap aiops-agent-config -n aiops-harness -o jsonpath='{.data.agent-config\.yaml}'
```

Point out:
- `llama_stack_url` — the agent runtime endpoint
- `model_id` — which model investigates inside the pipeline
- `eval_model_url` / `eval_model_id` — the external eval model (must be different from the pipeline)
- `tools` — the investigative tools available to the agent
- `system_prompt` — the SRE agent's instructions

## 3. Run Benchmark (4-5 min)

```bash
python3 scripts/local_benchmark.py
```

Narrate the phases as they execute:
1. **Inject** — CPU stress sidecar into `reviews-v2` (95% CPU for 10 minutes)
2. **Propagation** (120s) — Wait for fault to manifest in metrics and events
3. **Investigate** — Each model runs independently through Llama Stack, making tool calls
4. **Score** — Deterministic binary gate (RCA Detected) + external eval model assessment (RCA Eval)
5. **Track** — Results logged to MLFlow (AIOps instance for pipeline behavior, Harness instance for eval results)
6. **Cleanup** — Stress sidecar removed, services recover

## 4. Show Results (2-3 min)

```bash
python3 scripts/show_results.py
```

Walk through each section:

### Score breakdown
- **Detection** (10%) — Did it detect an incident?
- **Correlation** (10%) — Did it group related signals?
- **RCA Detected** (5%) — Binary gate: did it name the correct root cause? Automatic FAIL if missed.
- **RCA Eval** (50%) — External eval model scores investigation quality on four criteria: accuracy, evidence quality, reasoning coherence, remediation quality. This is the primary quality signal.
- **Action Safety** (10%) — Is the recommended remediation safe?
- **Auditability** (15%) — Can the reasoning be reconstructed from tool-call logs?

### Key point to emphasize
> "A model can pass the binary RCA gate by naming the right root cause, but still fail
> overall if the eval model determines the evidence was hallucinated or the reasoning
> was incoherent. Naming the right answer isn't enough — you have to show your work."

### MLFlow dashboards

```bash
oc get routes -n mlflow-aiops -o jsonpath='{.items[0].spec.host}' && echo ""
oc get routes -n mlflow-harness -o jsonpath='{.items[0].spec.host}' && echo ""
```

Open both MLFlow UIs:
- **AIOps MLFlow** — Pipeline behavior: model ID, tool calls, investigation time, RCA output
- **Harness MLFlow** — Evaluation results: six scoring dimensions, PASS/FAIL, eval model assessment

## 5. Key Takeaways (1-2 min)

- **Harness is external**: The AI model doesn't know it's being tested
- **Tool-based retrieval**: Agent queries evidence through structured tools, not raw telemetry dumps
- **Eval model is independent**: Exists outside the pipeline, only sees harness-provided data
- **Reproducible**: Same manifest, same injection, comparable scores across runs
- **Auditable**: Every tool call logged, every evidence pointer tracked
- **Tracked**: MLFlow captures every run for regression testing and model comparison
- **Portable**: Swap the model, the agent framework, or the eval model — the harness doesn't change

## Optional: Run Distributed Scenario (Scenario C)

```bash
python3 scripts/distributed_benchmark.py
```

This injects two faults with a 60-second stagger (CrashLoopBackOff in ratings-v1 at T+0, CPU saturation in reviews-v2 at T+60). Tests whether the agent can identify multiple independent root causes and understand temporal ordering.

## Cleanup

```bash
./scripts/90_cleanup.sh
```
