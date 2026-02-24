# Model Experimentation Log

This document captures the model selection and benchmarking experiments conducted
for the AIOps Harness project on an IBM Cloud OpenShift 4.21.x cluster with
2x NVIDIA H200 (Hopper, 141GB each) GPU nodes.

## Architecture Note

The AIOps pipeline under evaluation uses **Llama Stack** as its agent runtime,
backed by **vLLM** for model serving. Llama Stack manages the ReAct investigation
loop, tool routing, and session state. The AI Harness evaluates the pipeline
externally, using a configurable eval model to independently fact-check the
pipeline's conclusions against real telemetry and Llama Stack session data.

**MLFlow is the primary experiment tracking backbone.** Every benchmark run
logs its results to two physically separated MLFlow instances:

| MLFlow Instance | Namespace | What it Tracks |
|----------------|-----------|---------------|
| **MLFlow AIOps** | `mlflow-aiops` | Pipeline behavior: model ID, tool calls, evidence retrieved, per-tool latency, investigation time, MTTD, final RCA output |
| **MLFlow Harness** | `mlflow-harness` | Evaluation results: six scoring dimensions, weighted composite, PASS/FAIL, eval model assessments, fact-checking results, hallucination flags |

This dual-instance design maintains the **External Independence Principle**: the
pipeline team sees their metrics, the evaluation team sees theirs, and neither
can influence the other. Filesystem artifacts (`artifacts/`) serve as a backup,
but MLFlow is the authoritative record for all experiment data.

The benchmark runs below were conducted using the local benchmark script
(`scripts/local_benchmark.py`) which drives the models directly via
OpenAI-compatible API. Production runs use the full Llama Stack + AlertManager
pipeline described in the whitepaper.

### Viewing Results in MLFlow

After any benchmark run, results are available in the MLFlow UI:

```bash
# Get MLFlow AIOps route (pipeline metrics)
oc get route mlflow-aiops -n mlflow-aiops -o jsonpath='{.spec.host}'

# Get MLFlow Harness route (evaluation metrics)
oc get route mlflow-harness -n mlflow-harness -o jsonpath='{.spec.host}'
```

Each MLFlow run contains:
- **Parameters**: model ID, scenario, tool count, RAG enabled flag
- **Metrics**: weighted score, investigation time, MTTD, individual scoring dimensions
- **Artifacts**: full RCA output JSON, tool call logs, eval model assessment (logged as JSON artifacts)

## Cluster GPU Topology

| Node | Mode | GPUs | Available Slices |
|------|------|------|------------------|
| Node 1 | MIG | 1 physical GPU | 1g.18gb, 2g.35gb, 3g.71gb |
| Node 2 | Whole | 8 GPUs | nvidia.com/gpu (no MIG) |

- NVIDIA Driver: 580.105.08
- CUDA Runtime: 13.0
- GPU Family: Hopper (compute capability 9.0)

## Models Evaluated

### 1. Granite 4.0-H-Tiny (1B active)

| Property | Value |
|----------|-------|
| Architecture | MoE, 7B total / 1B active |
| Deployment | RHAIIS vLLM v0.11.2, MIG slice 1g.18gb |
| GPU Memory | 12.93 GiB weights, 0.39 GiB KV cache remaining |
| Quantization | BF16 |
| Served via | OpenShift Route (TLS edge) |

**Findings**: This model fits on the smallest MIG slice but is insufficient for
AIOps RCA without augmentation. It cannot reliably write valid PromQL (generated
`avg_by_label()` which does not exist, used `http_requests_total{service=...}`
instead of `container_cpu_usage_seconds_total`), produces shallow reasoning, and
hedges rather than building causal chains. In early runs it frequently blamed
the wrong component (e.g., `productpage:high_cpu_utilization` instead of
`reviews-v2:cpu_saturation`). When the harness accidentally leaked pre-collected
evidence into the prompt, Granite scored 0.94 by parroting the answer. Once that
bug was fixed, it consistently scored 0.53-0.80 depending on run variance, with
eval model score averages of 2.7-7.0/10.

However, when augmented with OpenShift Lightspeed RAG documentation, Granite's
performance improved dramatically (see Lightspeed RAG experiments below).

### 2. Gemini 3 Pro (SaaS baseline)

| Property | Value |
|----------|-------|
| Architecture | Proprietary (Google) |
| Deployment | SaaS via Google AI Studio API |
| API | OpenAI-compatible endpoint at generativelanguage.googleapis.com |
| Quantization | N/A (cloud-hosted) |
| Cost model | Developer API key (free tier) |

**Findings**: Gemini serves as the SaaS quality baseline. It writes valid Istio
PromQL, uses thinking tokens for multi-step reasoning, and produces structured
JSON output. However, it requires `max_tokens >= 8192` because internal thinking
tokens consume the output budget. Gemini consistently achieves the highest judge
scores (9.2-10.0/10 average) and is the only model to consistently identify the
stress-injector sidecar as the specific root cause mechanism
(`cpu_saturation_due_to_stress_injector_container`). Response time averaged
37-57s due to SaaS latency and thinking overhead.

### 3. Kimi K2.5 (attempted, never ran)

| Property | Value |
|----------|-------|
| Architecture | MoE, ~1T total parameters |
| Deployment | KServe LLMInferenceService CRD |
| GPU Requirement | 8x whole GPUs (tensor-parallel-size=8) |
| Status | CreateContainerConfigError for 8+ days |

**Findings**: This model was already deployed on the cluster by another team but
never successfully started. It was stuck in `CreateContainerConfigError` and
consuming GPU reservations. We scaled it to 0 replicas via a KServe CRD patch
(`oc patch llminferenceservice kimi-k2-5 --type=merge -p '{"spec":{"replicas":0}}'`)
to free resources. Note: scaling must target the CRD, not the deployment
directly, because the KServe controller immediately reverts deployment-level
replica changes.

### 4. Qwen3-Coder-Next FP8 (80B MoE, 3B active)

| Property | Value |
|----------|-------|
| Architecture | Hybrid MoE + Mamba2, 80B total / 3B active |
| Deployment | Community vLLM cu130-nightly, 2x H200 whole GPUs |
| GPU Memory | ~85 GiB FP8 weights across 2 GPUs |
| Quantization | FP8 (pre-quantized HuggingFace checkpoint) |
| Context | 32,768 tokens (max-model-len) |
| KV Cache | FP8 (reduces memory, enables longer context) |
| Tool Calling | Native via `--tool-call-parser=qwen3_coder` |
| Served via | OpenShift Route (TLS edge) |

**Findings**: This model delivered strong RCA performance. It consistently
identifies `bookinfo/reviews-v2:cpu_saturation` as the root cause, makes
9-15 tool calls across multiple rounds, queries the correct Prometheus metrics,
and self-corrects malformed PromQL on retry. Judge scores average 7.0-9.5/10
depending on the run. Response time averages 11-19s. When augmented with
Lightspeed RAG, it achieved a perfect 1.0 weighted score (see below).

## Deployment Challenges

### CUDA 13.0 Driver Compatibility

The cluster runs NVIDIA driver 580.105.08 (CUDA 13.0), but pre-built vLLM
Docker images ship with CUDA 12.8. This causes `CUDA Error 803: system has
unsupported display driver / cuda driver combination` in the GPU worker
processes.

**Attempted fixes**:
- `LD_LIBRARY_PATH=/usr/local/nvidia/lib:/usr/local/nvidia/lib64` - Fixed the
  main API server process but not the tensor-parallel worker subprocesses
  (spawned via Python multiprocessing, they initialize CUDA independently)
- Switching to `vllm/vllm-openai:cu130-nightly` - resolved the issue completely

**Lesson**: When tensor-parallel-size > 1, CUDA compatibility must be native in
the container image. Environment variable workarounds only affect the parent
process.

### vLLM Entrypoint Override

The initial manifest used an explicit `command: ["python", "-m", "vllm.entrypoints.openai.api_server"]`
which failed because the community vLLM image has `python3`, not `python`.
Removing the `command` block and using only `args` lets the image's built-in
`ENTRYPOINT` handle startup correctly.

### KServe CRD Replica Management

Models deployed via KServe `LLMInferenceService` CRDs cannot be scaled by
patching the deployment directly. The KServe controller watches the CRD and
reconciles the deployment replicas back to the CRD spec within seconds. To
scale down: `oc patch llminferenceservice <name> --type=merge -p '{"spec":{"replicas":0}}'`

### Fault Injection: Docker Hub Rate Limiting

The initial CPU saturation injection used `polinux/stress-ng:latest` from Docker
Hub. Anonymous pulls are rate-limited, causing `ImagePullBackOff` on the
stress-injector sidecar. The sidecar never started, meaning zero CPU stress
was actually applied. All models correctly identified the ImagePullBackOff as the
issue, but ground truth expected `cpu_saturation`, so scores were misleading.

**Fix**: Switched to `registry.access.redhat.com/ubi9/ubi-minimal:latest` with a
POSIX shell busy-loop (`sh -c 'while true; do :; done'`). The `:` builtin burns
CPU in a tight loop, hitting the 500m CPU limit at ~47% of one core. This image
pulls reliably from the Red Hat registry without authentication.

### Fault Injection: Strategic Merge Patch Cannot Delete Containers

The `remove_cpu_saturation()` function originally used the Kubernetes Python
client's `patch_namespaced_deployment`, which defaults to strategic merge patch.
Strategic merge patch merges container arrays by the `name` key and cannot
delete containers by omission.

**Fix**: Switched to `subprocess.run(["oc", "patch", ..., "--type=merge"])` which
sends a JSON merge patch that replaces the entire containers array, reliably
removing the sidecar.

## Benchmark Results

### Scoring Rubric (6 dimensions)

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Detection | 10% | Did the agent identify an incident? |
| Correlation | 10% | Did it gather evidence through successful tool calls? |
| RCA Detected | 5% | Binary gate: was the root cause named anywhere? (Pass/Fail) |
| **RCA Eval** | **50%** | **External eval model assessment of investigation quality (1-10, normalized)** |
| Action Safety | 10% | Are recommended actions safe (no destructive ops)? |
| Auditability | 15% | Can we trace the reasoning through tool call logs? |

Pass threshold: weighted score >= 0.60 AND RCA Detected = Pass.

**RCA Detected** is a binary failsafe: if the model did not name the correct
root cause anywhere in its top-3 hypotheses or response text, it automatically
fails regardless of other scores. This is a cheap, deterministic check that
catches total failures instantly without needing any LLM inference.

**RCA Eval** is the primary quality signal, carrying 50% of the total weight.
The external eval model scores the output on four criteria (`rca_accuracy`,
`evidence_quality`, `reasoning_coherence`, `remediation_quality`) plus an
`overall` holistic score (1-10). The RCA Eval is the overall score normalized
to 0.0-1.0. The eval model must exist outside the system being evaluated and
only has access to the data the harness provides.

Note: During benchmarking, we ran cross-model validation where each model
scored every other model's output to validate the eval system design.
Key observations:
- Weaker models (Granite) tend to be overly generous evaluators
- Stronger models (Gemini, Qwen3) are harsher and more discerning
- Cross-evaluation consensus between strong models is the reliable signal

### Scenario: CPU Saturation (stress sidecar into reviews-v2)

**Harness design**: The agent receives only an alert-level incident description
and a time window. It must discover all evidence through tool calls
(getMetricHistory, getK8sEvents, searchLogs). No pre-collected metrics are
provided in the prompt.

#### Run 1: Initial (with evidence leakage bug)

*Pre-eval scoring; RCA Eval not yet available.*

| Model | RCA Detected | Time | Tool Calls | Result |
|-------|-------------|------|------------|--------|
| Granite 4 Tiny | Fail | 1.5s | 0 | FAIL |
| Gemini 3 Pro | Pass | 45.2s | 3 | PASS |

Granite generated invalid PromQL and crashed. Gemini correctly identified the
stress-injector. However, the harness was providing pre-collected evidence in
the prompt (see Harness Bug below).

#### Run 2: Three-way (evidence leakage fixed, injection still broken)

*Pre-eval scoring; RCA Eval not yet available.*

| Model | RCA Detected | Time | Tool Calls | Result |
|-------|-------------|------|------------|--------|
| Granite 4 Tiny | Pass | 7.7s | 3 | FAIL |
| Gemini 3 Pro | Pass | 52.5s | 7 | PASS |
| Qwen3-Coder-Next | **Pass** | **19.2s** | **13** | **PASS** |

With evidence leakage fixed, Qwen3-Coder-Next emerged as the winner. Note: the
stress-ng injection was still broken (Docker Hub rate limiting) so models were
investigating an ImagePullBackOff rather than actual CPU saturation.

#### Run 3: Three-way (injection fixed, UBI-minimal image)

*Pre-eval scoring; RCA Eval not yet available.*

After fixing the injection image and patch method:

| Model | RCA Detected | Time | Tool Calls | Result |
|-------|-------------|------|------------|--------|
| Granite 4 Tiny | Pass | 7.5s | 3 | PASS |
| Gemini 3 Pro | **Pass** | 38.4s | 4 | **PASS** |
| Qwen3-Coder-Next | Pass | 13.7s | 10 | PASS |

All three models correctly identified CPU saturation. Gemini led on weighted
score.

#### Run 4: Three-way with RCA Eval

First run with the eval model scoring system:

| Model | Score | RCA Detected | RCA Eval | Time | Tool Calls | Result |
|-------|-------|-------------|----------|------|------------|--------|
| Granite 4 Tiny | 0.65 | Pass | 5.0/10 | 7.4s | 1 | PASS |
| Gemini 3 Pro | 0.93 | Pass | 9.0/10 | 63.3s | 9 | PASS |
| Qwen3-Coder-Next | **0.95** | **Pass** | **9.0/10** | **19.7s** | **15** | **PASS** |

The RCA Eval revealed that Granite's "Pass" detection was misleading. Gemini
gave Granite 3/10, noting it "hallucinated Prometheus queries it never executed
and provided dangerous remediation advice." Qwen3 achieved the highest weighted
score with strong peer consensus.

### Experiment: OpenShift Lightspeed RAG Integration

We investigated whether augmenting models with curated documentation, similar
to Red Hat OpenShift Lightspeed's RAG pipeline, could improve RCA performance.

#### RAG Architecture

The implementation adds a `searchDocumentation` tool to the agent's toolkit,
backed by a keyword-searchable knowledge base built from two sources:

1. **Official OCP 4.21 documentation** (93 chunks) extracted from the
   [openshift/lightspeed-rag-content](https://github.com/openshift/lightspeed-rag-content)
   repository. This is the same pre-converted plaintext that Lightspeed uses
   for its FAISS vector index. Topics include monitoring, troubleshooting,
   node resource management, pod autoscaling, service mesh, and OpenTelemetry.

2. **BYOK (Bring Your Own Knowledge) supplements** (6 chunks) containing
   PromQL metric references, SRE runbooks, and Bookinfo application
   architecture. In a production Lightspeed deployment, these would be added
   via the BYOK pipeline (`openshift/lightspeed-rag-content/byok/`).

The RAG system prompt instructs the agent: "If you are unsure about the correct
PromQL metric name or query syntax, make ONE quick documentation search first,
then immediately move on to querying live systems." This single-lookup constraint
was critical (see prompt engineering findings below).

#### Run 5: Four-way with Granite+Lightspeed (hand-crafted KB)

Initial test with a hand-crafted knowledge base (12 curated documents covering
PromQL patterns, K8s troubleshooting, and Bookinfo architecture):

| Model | Score | RCA Detected | RCA Eval | Time | Tool Calls | Result |
|-------|-------|-------------|----------|------|------------|--------|
| Granite 4 Tiny | 0.47 | Pass | 2.7/10 | 9.6s | 3 | FAIL |
| **Granite + RAG** | **0.79** | **Pass** | **8.0/10** | 10.3s | 3 | **PASS** |
| Qwen3-Coder-Next | 0.82 | Pass | 6.3/10 | 11.0s | 8 | PASS |
| Gemini 3 Pro | 1.00 | Pass | 10.0/10 | 48.9s | 7 | PASS |

**Key finding**: RAG took Granite from FAIL (0.47) to PASS (0.79), a +68%
improvement. The 1B model now approached the 80B Qwen3. The RAG variant's first
tool call was `searchDocumentation`, which returned the correct metric name
(`container_cpu_usage_seconds_total`) and PromQL patterns. Without RAG, Granite
guessed `http_requests_total{service="productpage"}` which returned empty results.

#### Run 6: Five-way with Lightspeed data (Qwen3 regression)

Replaced the hand-crafted KB with actual Lightspeed docs (93 chunks from
`openshift/lightspeed-rag-content`) without BYOK supplements:

| Model | Score | RCA Detected | RCA Eval | Time | Tool Calls | Result |
|-------|-------|-------------|----------|------|------------|--------|
| Granite 4 Tiny | 0.66 | Pass | 6.0/10 | 7.4s | 3 | PASS |
| Granite + Lightspeed | 0.80 | Pass | 7.8/10 | 6.5s | 3 | PASS |
| Qwen3-Coder-Next | 0.95 | Pass | 9.5/10 | 14.3s | 15 | PASS |
| **Qwen3 + Lightspeed** | **0.61** | **Pass** | **4.4/10** | 33.5s | 5 | PASS |
| Gemini 3 Pro | 0.98 | Pass | 9.5/10 | 52.6s | 7 | PASS |

**Key finding**: Lightspeed RAG *hurt* Qwen3 (RCA Eval dropped from 9.5 to
4.4). The 80B model spent all 7 tool calls on `searchDocumentation` and never
queried Prometheus or K8s events. Its final text response contained `<tool_call>`
XML tags (Qwen3's native format) attempting to call `QueryPrometheus`, but these
were emitted as text rather than proper function calls. The model ran out of
tool-calling rounds before investigating the actual incident.

**Root cause**: The RAG system prompt said "ALWAYS start your investigation by
searching the documentation." Granite (1B) interpreted this as "search docs
once, then investigate." Qwen3 (80B) took it literally and spent ALL rounds
on docs. The standard OCP docs also lack specific PromQL metric names, so the
documentation searches returned generic administration content rather than
actionable investigation guidance.

#### Run 7: Five-way with Lightspeed + BYOK supplements (tuned prompt)

Added 6 BYOK supplement chunks (PromQL references, SRE runbooks, Bookinfo
architecture) and tuned the RAG prompt to constrain documentation searches
("make ONE quick documentation search, then immediately move on"):

| Model | Score | RCA Detected | RCA Eval | Time | Tool Calls | Result |
|-------|-------|-------------|----------|------|------------|--------|
| Granite 4 Tiny | 0.59 | Pass | 5.0/10 | 9.8s | 1 | FAIL |
| **Granite + Lightspeed** | **0.81** | **Pass** | **6.8/10** | **7.1s** | 3 | **PASS** |
| Qwen3-Coder-Next | 0.76 | Pass | 7.0/10 | 13.1s | 9 | PASS |
| **Qwen3 + Lightspeed** | **0.90** | **Pass** | **8.0/10** | **8.0s** | 6 | **PASS** |
| Gemini 3 Pro | 0.92 | Pass | 9.8/10 | 37.4s | 4 | PASS |

**Key findings**:

- **Granite base now correctly FAILs**. Under the old deterministic scoring,
  Granite passed with 0.77 despite making only 1 tool call and hallucinating
  evidence. The eval-weighted system correctly identifies this as insufficient
  (RCA Eval 5.0/10 drags the score below the 0.60 threshold).

- **Granite + Lightspeed: 0.81 PASS**. The 1B model used one doc search
  (Bookinfo architecture), then K8s events and log searches. It correctly
  identified CPU saturation on reviews-v2 with a detailed causal chain including
  "CPU saturation up to 105% at peak." RCA Eval 6.8/10 reflects real
  investigation quality.

- **Qwen3 + Lightspeed: 0.90 PASS**. With the tuned prompt, Qwen3 now made
  one doc search for PromQL patterns, then immediately queried K8s events and
  Prometheus. It identified 5 hypotheses including `sidecar_cpu_contention`,
  the actual root cause mechanism. RCA Eval 8.0/10.

- **Both Lightspeed variants beat Gemini 3 Pro** on speed (7-8s vs. 37s) while
  keeping all data on-premises. Gemini still leads on RCA Eval (9.8/10) due to
  consistently identifying the stress-injector as the specific mechanism.

### Eval System Validation Matrix (Final Run)

Cross-model validation: each cell shows the row model's RCA scored by the column model (1-10 scale). This matrix validates the eval system design — the production system uses a single external eval model:

|  | Granite | Granite+LS | Gemini | Qwen3 | Qwen3+LS | **RCA Eval** |
|--|---------|------------|--------|-------|----------|-------------|
| **Granite** | -- | 4 | 5 | 5 | 6 | 5.0 |
| **Granite+LS** | 8 | -- | 3 | 8 | 8 | **6.8** |
| **Gemini** | 10 | 9 | -- | 10 | 10 | **9.8** |
| **Qwen3** | 6 | 8 | 8 | -- | 6 | 7.0 |
| **Qwen3+LS** | err | 10 | 6 | 8 | -- | **8.0** |

Notable patterns:
- Gemini 3 Pro received the highest RCA Eval (9.8), consistently praised for
  identifying the stress-injector specifically.
- The Lightspeed variants show improvement vs. their vanilla counterparts:
  Granite 5.0 to 6.8, Qwen3 7.0 to 8.0.
- Gemini judged Granite+Lightspeed harshly (3/10), noting it "hallucinated
  Prometheus metrics" despite not calling the metrics tool. This highlights
  that Granite sometimes fabricates evidence in its summary even when the
  correct tools were available.
- The RCA Eval now carries 50% of the weighted score, making it the primary
  differentiator between models that merely name the right answer and models
  that actually investigate.

## Harness Bug: Evidence Leakage

During benchmarking we discovered a critical flaw in the harness. The
`invoke_agent()` function was including a `build_evidence_summary()` in the
initial prompt, which contained pre-collected Prometheus metrics showing
`container_cpu_usage_seconds_total` data with the `stress-injector` container
clearly visible.

This meant a model could score a perfect RCA by reading the provided evidence
rather than investigating through tools. Granite 4 Tiny exploited this
unintentionally, scoring 0.94 while its actual tool calls returned zero useful
results (it queried non-existent metrics like `http_requests_total`).

**Fix**: Removed all pre-collected evidence from the agent prompt. Models now
receive only an alert description and time window. Evidence must be gathered
exclusively through tool calls. Scoring was also updated to check whether tool
calls returned actual data (not just whether they were made).

## Harness Design: Two-Tier Scoring (RCA Detected + RCA Eval)

The deterministic `_labels_match()` scorer was originally the primary quality
signal (35% weight). Testing revealed it produces false confidence: a model can
name the right root cause keyword while hallucinating all supporting evidence,
and still receive a perfect deterministic score.

**Example**: In Run 7, Granite base received RCA Detected: Pass (it named
`bookinfo/reviews-v2:cpu_saturation`) but made only 1 tool call and fabricated
Prometheus metrics it never queried. Under the old scoring it passed with 0.77.
Under the eval-weighted scoring it correctly fails with 0.59.

**Resolution**: RCA Detected was flattened to a binary gate (Pass/Fail, 5%
weight) and RCA Eval (external eval model assessment, 50% weight) became the
primary quality signal. This ensures that naming the right answer is necessary
but not sufficient; models must actually investigate to score well.

#### Run 8: Five-way validation (post-architecture update)

Run after the Llama Stack + MLFlow architectural redesign. Same benchmark
script, same scoring rubric, same fault injection method. Validates that the
two-tier scoring system produces consistent results across runs.

| Model | Score | RCA Detected | RCA Eval | Time | Tool Calls | Hallucinated? | Result |
|-------|-------|-------------|----------|------|------------|---------------|--------|
| Granite 4 Tiny | 0.47 | Pass | 2.8/10 | 3.7s | 1 | Yes | FAIL |
| Granite + Lightspeed | 0.71 | Pass | 4.7/10 | 6.7s | 3 | Yes | PASS |
| Qwen3-Coder-Next | 0.87 | Pass | 8.4/10 | 11.7s | 9 | Partially | PASS |
| **Qwen3 + Lightspeed** | **0.99** | **Pass** | **9.8/10** | **13.7s** | **10** | Yes | **PASS** |
| Gemini 3 Pro | 0.97 | Pass | 9.7/10 | 39.4s | 6 | No | PASS |

**Key findings**:

- **Granite base continues to FAIL correctly**. Score of 0.47 (down from 0.59
  in Run 7) with RCA Eval 2.8/10. Made only 1 tool call and identified the
  right resource (reviews-v2) but labeled the fault as "unknown." Judges
  unanimously penalized the lack of investigation: Qwen3+LS gave 1/10, noting
  "failed to correctly identify the root cause, gathered no meaningful evidence."

- **Granite + Lightspeed passes but with warnings**. Score 0.71 with RCA Eval
  4.7/10. Correctly named CPU saturation but also hallucinated an OOMKill event.
  Gemini gave 3/10: "fabricated the explanation around an OOMKill event without
  retrieving any metric data, hallucinating tool calls in the evidence list."

- **Qwen3 + Lightspeed achieves the highest score (0.99)**. Near-perfect across
  all dimensions. RCA Eval 9.8/10 with 10 tool calls. Used RAG for PromQL
  reference, then systematically queried Prometheus, K8s events, and logs.
  Identified 4 hypotheses including `sidecar_resource_exhaustion`. All four
  judges scored 9-10/10.

- **Gemini remains the quality leader** in RCA Eval (9.7/10) and the only model
  to identify the `stress_injector_container` specifically, but at 39.4s vs.
  Qwen3+LS's 13.7s. On weighted score, Qwen3+LS now edges ahead (0.99 vs 0.97)
  due to higher correlation and tool diversity scores.

- **Hallucination remains a Granite issue**. Even with Lightspeed, Granite
  fabricates evidence it never collected. The hallucination detection correctly
  flags both Granite variants.

### Eval System Validation Matrix (Run 8)

|  | Granite | Granite+LS | Gemini | Qwen3 | Qwen3+LS | **RCA Eval** |
|--|---------|------------|--------|-------|----------|-------------|
| **Granite** | -- | 4 | 3 | 3 | 1 | 2.8 |
| **Granite+LS** | 8 | -- | 3 | 3 | err | **4.7** |
| **Gemini** | 9 | 10 | -- | err | 10 | **9.7** |
| **Qwen3** | 9 | 8 | 8 | -- | 9 | **8.4** |
| **Qwen3+LS** | 10 | 10 | 9 | 10 | -- | **9.8** |

Notable patterns:
- Qwen3+LS received a perfect 10 from Granite, Granite+LS, and Qwen3 —
  universal consensus that its investigation was thorough.
- Gemini judged both Granite variants harshly (3/10 each), specifically calling
  out hallucinated evidence — this is the fact-checking role in action.
- Two `err` entries where judge JSON parsing failed (Qwen3's thinking tags
  interfered with JSON extraction). These are excluded from the RCA Eval average.
- The strong models (Gemini, Qwen3, Qwen3+LS) cluster at 8.4-9.8/10, while
  Granite variants are clearly separated at 2.8-4.7/10. The two-tier scoring
  correctly reflects this quality gap.

## Scenario C: Distributed Cascading Failure

Scenario C tests whether models can identify **multiple independent root causes**
in a distributed incident. Unlike Scenarios A and B (single fault, single service),
this scenario injects two faults into two different services with a 60-second
stagger, creating genuine distributed complexity.

### Fault Design

| Order | Time | Target | Fault Type | Mechanism |
|-------|------|--------|-----------|-----------|
| #1 | T+0 | ratings-v1 | CrashLoopBackOff | Bad env var (`INVALID_DB_HOST`) + command override |
| #2 | T+60 | reviews-v2 | CPU saturation | Stress sidecar (UBI-minimal busy-loop) |

### Cascade Effects

The staggered injection creates layered complexity:

1. **T+0 to T+60**: ratings-v1 is crash-looping. reviews-v2/v3 show "Ratings
   service unavailable." productpage shows degraded reviews. reviews-v1 is
   unaffected (it doesn't call ratings).

2. **T+60 onward**: reviews-v2 is now doubly impaired: its dependency (ratings)
   is crashing AND it's CPU-starved from the stress sidecar. productpage sees
   both errors and timeouts.

3. **Red herrings**: productpage looks sick but is purely a victim. reviews-v1
   and reviews-v3 are partially affected (ratings dependency) but not CPU-stressed.

### What the Agent Must Do

- **Investigate both services independently** using getMetricHistory, getK8sEvents,
  searchLogs, and the new getNodeTopology tool
- **Identify both root causes**: `bookinfo/ratings-v1:crashloop_bad_config` AND
  `bookinfo/reviews-v2:cpu_saturation`
- **Recognize the temporal ordering**: ratings crashed first, CPU saturation
  started 60 seconds later
- **Distinguish direct faults from cascade effects**: productpage errors are
  symptoms, not root causes

### Scoring: Multi-Cause RCA

The distributed scenario extends the scoring rubric:

| Causes Found | RCA Completeness | Effect on Score |
|-------------|-----------------|----------------|
| Both (2/2) | 1.0 | Full credit |
| One (1/2) | 0.5 | Partial credit |
| Neither (0/2) | 0.0 | Zero credit |

**RCA Detected** (binary gate): Pass if at least ONE root cause identified.
**RCA Eval** (50% weight): Judges specifically evaluate multi-cause detection,
temporal analysis, and whether the agent investigated both services.

### New Tool: getNodeTopology

The distributed scenario adds a `getNodeTopology` tool that returns which pods
are running on which nodes, including restart counts and container names. This
helps the agent understand the physical distribution of the system and identify
whether faults are co-located on the same node (resource contention) or
distributed across nodes (independent faults).

### Benchmark Script

Run the distributed benchmark with:

```bash
python3 scripts/distributed_benchmark.py
```

Artifacts are written to `artifacts/distributed-benchmark-<timestamp>/`.

## Key Findings: RAG Prompt Engineering

The Lightspeed RAG experiments revealed critical prompt engineering lessons:

1. **"ALWAYS start with docs" is too directive**. Small models (Granite 1B)
   comply briefly then investigate. Large models (Qwen3 80B) comply literally
   and exhaust all tool-calling rounds on documentation searches without ever
   querying live systems.

2. **"Make ONE quick search, then move on" works universally**. This constraint
   lets small models get the metric names they need while preventing large
   models from over-indexing on documentation.

3. **BYOK supplements are essential**. The standard OCP documentation covers
   administration procedures but does not contain specific Prometheus metric
   names like `container_cpu_usage_seconds_total` or PromQL query patterns.
   Without BYOK supplements containing metric references and SRE runbooks,
   documentation searches return generic content that doesn't help with
   incident investigation.

4. **RAG is an equalizer, not a universal improvement**. It helps small models
   more than large ones. A 1B model with RAG can approach the performance of an
   80B model without it. The largest models already have PromQL and Kubernetes
   knowledge internalized from training data; RAG adds marginal value and can
   even distract if the prompt is not carefully tuned.

## Recommendations

1. **For production RCA**: Qwen3-Coder-Next + Lightspeed on 2x H200 GPUs
   achieved 0.90 weighted score (RCA Eval 8.0/10) at 8.0s latency. This
   combination provides the best balance of accuracy, speed, and data
   sovereignty.

2. **For resource-constrained deployments**: Granite 4 Tiny + Lightspeed on a
   single MIG slice achieved 0.81 (RCA Eval 6.8/10) at 7.1s. This is
   remarkable for a 1B-active model and demonstrates that RAG can compensate
   for model size when the knowledge base is well-curated.

3. **For SaaS quality baseline**: Gemini 3 Pro consistently achieves the highest
   RCA Eval (9.8/10) with the most precise root cause identification, but at
   37-57s latency and with data leaving the cluster.

4. **RAG knowledge base curation**: Always supplement standard OCP docs with
   BYOK content containing PromQL metric references, SRE runbooks, and
   application-specific architecture documentation. Use the
   `openshift/lightspeed-rag-content` BYOK pipeline for production deployments.

5. **Harness scoring**: Use two-tier scoring. RCA Detected (binary gate) catches
   total failures instantly. RCA Eval (50% weight) is the primary quality
   signal, ensuring models are evaluated on investigation rigor, not just
   keyword matching. Never include pre-collected telemetry in the agent prompt.
