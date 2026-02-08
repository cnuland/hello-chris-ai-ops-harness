# AIOps Harness Demo — Video Recording Guide

Step-by-step walkthrough. Every command is copy-paste ready.
Total runtime: ~10-12 minutes on camera (assuming infrastructure is already deployed).

---

## Before You Hit Record

Make sure everything is deployed and healthy. Run these checks:

```bash
# Verify you're logged in
oc whoami

# Check all pods are running
oc get pods -n llm-serving       # granite-4-server should be 1/1 Running
oc get pods -n bookinfo           # all 7 pods should be 1/1 Running
oc get pods -n aiops-harness      # aiops-tools-server should be 1/1 Running

# Quick health check on the tools server
oc exec deploy/aiops-tools-server -n aiops-harness -- curl -s http://localhost:8000/healthz

# Quick health check on vLLM
oc exec deploy/granite-4-server -n llm-serving -- curl -s http://localhost:8080/v1/models
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
fault injection, evidence capture, and scoring — the AI model only sees evidence through
structured tool calls."

### Show what's running

```bash
# Show the cluster
oc whoami
oc whoami --show-server
```

```bash
# Show all four namespaces
oc get pods -n llm-serving -o wide
oc get pods -n bookinfo
oc get pods -n aiops-harness
```

**Talk track:** "We have four components:
1. **vLLM** serving IBM Granite 4 on an A100 GPU — this is our AI 'brain'
2. **Bookinfo** — the system under test, a microservices app with 6 services
3. **Tools Server** — a FastAPI gateway to Prometheus metrics, K8s events, and logs
4. **Harness Runner** — the orchestrator that runs as a Kubernetes Job"

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

## Part 2: Show the Harness Manifest (1 min)

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
- `scoring.weights` — RCA is weighted heaviest at 35%
- `scoring.passThreshold: 0.60` — the minimum score to pass

**Talk track:** "The harness knows the ground truth because it controls the injection.
The AI model does NOT know what was injected — it has to figure it out using tools."

---

## Part 3: Show the Tools Contract (1 min)

**Talk track:** "The AI model doesn't get raw telemetry dumped into its context.
Instead, it gets tool definitions — functions it can call to query evidence."

```bash
# Show the tools server endpoints
oc exec deploy/aiops-tools-server -n aiops-harness -- curl -s http://localhost:8000/openapi.json | python3 -m json.tool | head -40
```

Or just describe them verbally:
- `getMetricHistory` — runs PromQL queries against Prometheus/Thanos
- `getK8sEvents` — fetches Kubernetes events (pod crashes, restarts, scheduling)
- `searchLogs` — searches pod logs for error patterns
- `getTraceWaterfall` — distributed tracing (placeholder)

**Talk track:** "This is the tool-based retrieval pattern from the whitepaper.
The model decides what to query, the tools server executes it, and the results
come back as structured data. This keeps the evidence auditable."

---

## Part 4: Run the CrashLoopBackOff Scenario (3-4 min)

**Talk track:** "Let's run it. The harness will: capture a baseline, inject a fault,
wait for it to propagate, collect evidence, invoke the AI agent, score the result,
and clean up."

```bash
./scripts/21_run_harness_crashloop.sh
```

The script will stream logs in real time. Narrate each phase as it appears:

| Log line | What to say |
|----------|-------------|
| `Phase 1: Baseline (30s)` | "Capturing healthy-state metrics so we have a comparison point" |
| `Phase 2: Inject fault` | "Now injecting a bad environment variable into ratings-v1 — this will cause a CrashLoopBackOff" |
| `Injected CrashLoopBackOff into bookinfo/ratings-v1` | "The fault is in. The harness knows exactly what it did." |
| `Phase 3: Waiting for fault propagation (60s)` | "Waiting for Kubernetes to detect the crash and start restart cycling" |
| `Phase 4: Capture evidence` | "Now querying Prometheus for CPU, memory, restart counts, and pulling K8s events" |
| `HTTP Request: POST .../getMetricHistory` | "Each of these is a real Prometheus query via our tools server" |
| `HTTP Request: POST .../getK8sEvents` | "Kubernetes events will show the CrashLoopBackOff reason" |
| `Phase 5: Invoke Llama Stack agent` | "Now sending the evidence summary plus tool definitions to Granite 4" |
| `HTTP Request: POST .../v1/chat/completions` | "The model is reasoning about the incident..." |
| `Agent tool call: ...` | "The model decided to make a tool call — it's investigating on its own" |
| `Phase 6: Score agent output` | "Comparing the agent's answer against ground truth" |
| `Phase 7: Cleanup injection` | "Removing the bad env var — ratings-v1 will recover" |
| `Score: 0.90  Result: PASS  RCA: 1.0` | "The agent correctly identified the root cause. 90% composite score, perfect RCA." |

While waiting during the baseline/propagation phases, you can show the fault in action:

```bash
# In a second terminal — show the pod crash-looping (run this during Phase 3)
oc get pods -n bookinfo -w
```

You'll see `ratings-v1` go to `CrashLoopBackOff` and then recover after Phase 7.

---

## Part 5: Fetch and Walk the Artifacts (2-3 min)

```bash
./scripts/30_fetch_artifacts.sh
```

**Talk track:** "The harness produces four contract artifacts. Let me walk through each one."

### run.json — Run metadata

```bash
cat artifacts/latest/run.json | python3 -m json.tool
```

Point out:
- `run_id` — unique identifier
- `timestamps` — every phase is timestamped for reproducibility
- `status: completed` — the run finished successfully

### truth.json — Ground truth

```bash
cat artifacts/latest/truth.json | python3 -m json.tool
```

Point out:
- `root_cause.label: bookinfo/ratings-v1:crashloop_bad_config` — this is what the harness injected
- `fault.parameters` — the exact env var and value
- "The model never sees this file. It's only used for scoring."

### aiops_output.json — What the AI produced

```bash
cat artifacts/latest/aiops_output.json | python3 -m json.tool
```

Point out:
- `incident_summary` — the model's description of what happened
- `rca_ranked` — the model's root cause hypotheses (should include `crashloop_bad_config`)
- `recommended_action` — what the model suggests doing
- `tool_calls` — the actual tool calls the model made (fully logged)
- `evidence_links` — what evidence the model referenced

**Talk track:** "Notice the tool_calls array — this is the audit trail. We can see
exactly what the model queried and what it got back. This is the auditability
dimension of the scoring rubric."

### score.json — The scorecard

```bash
cat artifacts/latest/score.json | python3 -m json.tool
```

Point out each dimension:
- `detection: 1.0` — "Did it detect an incident? Yes."
- `correlation: 0.75` — "Did it group related signals? Mostly."
- `rca: 1.0` — "Did it identify the correct root cause? Yes, top-ranked."
- `action_safety: 0.7` — "Is the recommended action safe? Yes, no destructive commands."
- `auditability: 1.0` — "Can we reconstruct the reasoning? Yes, tool calls + evidence logged."
- `weighted_score: 0.9025` — "Composite score: 90.25%"
- `result: PASS` — "Above the 60% threshold with RCA above 50%."

---

## Part 6: Key Takeaways (1 min)

**Talk track:**

"Three things to take away:

1. **The harness is external.** The AI model doesn't know it's being tested.
   It gets an incident description and tools — that's it.

2. **Tool-based retrieval, not telemetry dumping.** The model queries what it needs
   through structured APIs. This keeps context windows manageable and evidence auditable.

3. **The scoring is reproducible.** Same manifest, same injection, comparable scores.
   You can swap the AI model — use a different LLM, a different provider — and the
   harness doesn't change. Only the scores change.

That's the external harness pattern for AIOps evaluation."

---

## Optional: Run CPU Saturation Scenario

If you have time, run the second scenario:

```bash
./scripts/20_run_harness_cpu.sh
```

This injects CPU stress into `reviews-v2` instead. Same flow, different fault type.
Fetch artifacts and compare scores:

```bash
./scripts/30_fetch_artifacts.sh
cat artifacts/latest/score.json | python3 -m json.tool
```

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
- The harness run takes ~2-3 minutes total (30s baseline + 60s propagation + evidence + agent + scoring)
