# Live Demo Script (10-15 minutes)

## Prerequisites (before the demo)
- OpenShift cluster logged in via `oc`
- vLLM serving Granite 4 model in `llm-serving` namespace (takes several minutes to download on first run)
- Bookinfo + tools server deployed via `./scripts/10_deploy_all.sh`
- Verify with: `./scripts/00_prereqs_check.sh`

## 1. Context (1-2 min)

Set the stage:

> "We're demonstrating an external AIOps test harness. The key principle: the harness
> must be independent from the AI system it evaluates. This ensures unbiased scoring,
> reproducibility, and auditability."

Show the architecture:
- **Bookinfo** = System Under Test (microservices app)
- **vLLM + Granite 4** = AI model serving with tool calling
- **Tools Server** = evidence gateway (Prometheus, K8s events, logs)
- **Harness Runner** = orchestrator (inject -> capture -> invoke -> score)

## 2. Show the HarnessManifest (1 min)

```bash
oc get configmap harness-manifest-crashloop -n aiops-harness -o yaml
```

Point out:
- Declarative scenario definition (fault type, target, parameters)
- Evidence contract (what evidence to collect)
- Scoring weights (detection 15%, correlation 15%, RCA 35%, action safety 20%, auditability 15%)

## 3. Run CrashLoopBackOff Scenario (3-4 min)

```bash
./scripts/21_run_harness_crashloop.sh
```

Narrate the phases as they execute:
1. **Baseline** (30s) - Capture healthy state metrics
2. **Inject** - Add bad env var `INVALID_DB_HOST` to `ratings-v1`
3. **Propagation** (60s) - Wait for CrashLoopBackOff to manifest
4. **Capture Evidence** - Query Prometheus metrics, K8s events, pod logs
5. **Invoke Agent** - Send evidence to Granite 4 with tool definitions
6. **Score** - Compare agent output against ground truth
7. **Cleanup** - Remove injected fault, restore normal operation

## 4. Fetch and Walk Artifacts (2-3 min)

```bash
./scripts/30_fetch_artifacts.sh
```

Walk through each artifact:

### `run.json` - Run metadata
- Run ID, timestamps for each phase, SUT info, final status

### `truth.json` - Ground truth
- The known root cause: `bookinfo/ratings-v1:crashloop_bad_config`
- Fault parameters that were injected

### `aiops_output.json` - Agent output
- Incident summary from the LLM
- Ranked root cause hypotheses
- Recommended remediation action
- Tool call log (what the agent queried and what it received)

### `score.json` - Rubric evaluation
- Category scores: detection, correlation, RCA, action safety, auditability
- Weighted composite score (typical: 0.90+)
- PASS/FAIL result against threshold

## 5. Key Takeaways (1-2 min)

- **Harness is external**: The AI model doesn't know it's being tested
- **Tool-based retrieval**: Agent queries evidence through structured tools, not raw telemetry dumps
- **Reproducible**: Same manifest, same injection, comparable scores across runs
- **Auditable**: Every tool call logged, every evidence pointer tracked
- **Portable**: Swap the "brain" (different model, different provider) without changing the harness

## Optional: Run CPU Saturation Scenario

```bash
./scripts/20_run_harness_cpu.sh
```

This injects CPU stress into `reviews-v2` and validates the agent can detect and attribute CPU saturation.

## Cleanup

```bash
./scripts/90_cleanup.sh
```
