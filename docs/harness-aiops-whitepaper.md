# Harness-First AIOps: An Architecture for Measurable Trust in AI-Driven Operations

| | |
|---|---|
| **Author** | Christopher Nuland |
| **Contributors** | Andreas Spanner |
| **Version** | Draft 3.0 |
| **Audience** | Platform engineers, SREs, AI/ML engineers, enterprise architects |
| **Reference Platform** | Red Hat OpenShift 4.21+, OpenShift AI, Llama Stack, vLLM (RHAIIS), MLFlow, OpenTelemetry |
| **Last Updated** | February 23rd, 2026 |

---

# Summary

AIOps is evolving from enhanced alerting into **closed-loop operational intelligence**: systems that can detect anomalies, understand their causes, recommend safe remediation, and get better over time. If you've operated any kind of distributed cloud-native system at scale, you already know why: the telemetry volume, service interdependence, and deployment velocity have fundamentally outpaced what human operators can handle with traditional monitoring tools.

The global AIOps platform market is projected to reach USD 32.4 billion by 2028, growing at a compound annual growth rate of 22.7% [15]. Organizations are investing heavily, and for good reason. A typical enterprise Kubernetes environment can generate millions of active metric time series, hundreds of gigabytes of log data per day, and millions of distributed trace spans per hour. When cascading failures propagate across service dependency graphs, you're staring at hundreds of correlated alerts and trying to figure out which ones are symptoms, which are causes, and which are just noise. That's a problem that doesn't scale with headcount.

Recent advances in generative and agentic AI make automated investigation feasible, but they introduce a critical concern:

> **Decisions may be produced without measurable correctness, reproducibility, or auditability.**

Without verifiable trust, AI-driven automation can't safely transition from experimentation to production. The core challenge isn't whether AI can reason about operations. It's whether that reasoning can be governed, measured, and improved systematically.

This white paper introduces a **harness-first AIOps architecture** that ensures:

- **Repeatability** through deterministic evaluation scenarios and versioned artifacts
- **Auditability** by grounding every AI conclusion in retrievable evidence and immutable run bundles
- **Safety** via external policy gates that constrain remediation behavior
- **Portability** across workloads, clusters, and AI models through an open harness contract
- **Continuous improvement** using reinforcement-style feedback loops executed in OpenShift AI

We demonstrate the architecture using Red Hat OpenShift 4.21+, OpenShift AI, Llama Stack (backed by vLLM), MLFlow, OpenTelemetry + Prometheus, and the Bookinfo microservices workload. Two reference scenarios (CPU saturation and CrashLoopBackOff) show how different fault types produce different evidence patterns while using the same harness contract, two-tier scoring system, and alert-triggered detection.

## What you'll get by the end

After reading this paper, you'll understand what an AIOps harness is and why it needs to be external to the AI system it evaluates. You'll know the four artifacts that every harness run produces (`run.json`, `truth.json`, `aiops_output.json`, `score.json`), how the six-dimension scoring rubric works (including external eval model assessment as the primary quality signal), and how the whole thing deploys on OpenShift. You'll also see how this pattern fits into compliance frameworks and how it creates a feedback loop for continuous model improvement.

---

# 1. The Evolution of AIOps

## 1.1 From Observability to Operational Intelligence

Traditional observability platforms were designed to answer a narrow question: *What is happening inside the system?* They give you dashboards, alerts, logs, and traces, providing visibility into runtime behavior.

But visibility alone doesn't create understanding. You still need to interpret signals, correlate symptoms across services, determine root cause, and decide how to remediate safely. That's a lot of cognitive load, and it only gets worse as your infrastructure grows.

The evolution of AIOps breaks down into three distinct generations, each responding to increasing operational complexity.

**First generation (2010-2018): Rule-based alerting and threshold monitoring.** Operations teams defined static thresholds for metrics like CPU utilization, memory consumption, and error rates. When a metric breached its threshold, an alert fired.

Simple and deterministic, but it scaled poorly. As infrastructure grew more dynamic through containerization, autoscaling, and microservices decomposition, static thresholds produced overwhelming volumes of alerts, many of them false positives or symptoms rather than root causes. A single cascading failure could trigger hundreds of alerts across dependent services, burying the actual root cause in noise.

**Second generation (2018-2023): Machine learning-based anomaly detection.** Platforms started applying unsupervised learning algorithms to detect statistical anomalies in telemetry streams, correlate related alerts into unified incidents, and reduce the noise that overwhelmed operations teams. Gartner coined the term "AIOps" in 2017 to describe this convergence of big data analytics and machine learning applied to IT operations data.

During this period, AIOps platforms focused on five core capabilities: cross-domain data ingestion, topology assembly and visualization, event correlation and pattern recognition, anomaly detection, and automated remediation triggering.

But a persistent challenge remained: figuring out the right resource constraints at the individual pod level. If you've worked with Kubernetes, you know this is one of the most common questions that comes up. As I documented in [Metrics-Driven Pod Constraints](https://www.redhat.com/en/blog/metrics-driven-pod-constraints), determining appropriate CPU and memory limits required metrics-driven analysis rather than guesswork, yet most platforms lacked the integration to connect constraint misconfiguration to downstream performance anomalies. You'd set high estimates, your app would run fine, but then the bill would arrive and you'd experience some serious sticker shock.

While a significant improvement over static thresholds, these second-generation systems remained fundamentally reactive. They could detect that something was anomalous but couldn't explain why or recommend what to do about it.

**Third generation (2023-present): LLM-powered agentic reasoning.** Rather than simply detecting anomalies, these systems attempt to understand *why* a system is behaving anomalously, identify the root cause, and recommend appropriate remediation. This marks a qualitative shift from pattern matching to causal reasoning. Agents can now conduct structured investigations, querying metrics, logs, traces, and Kubernetes events through tool interfaces to build an evidence-based diagnosis.

The Splunk State of Observability 2025 report [16], based on a survey of 1,855 ITOps and engineering professionals, found that top-performing organizations use emerging technologies like agentic AI four times more often than their peers and generate 53% higher return on observability investments.

So why does this matter for evaluation? Because these third-generation systems are making diagnostic *judgments*, not just detecting threshold violations. And judgments need to be verified.

Modern cloud-native systems introduce pressures that make this verification essential:

- **Rapid topology change** from autoscaling, rolling deployments, canary releases, and service mesh routing means the system you're observing is a moving target.
- **Extremely high telemetry cardinality** overwhelms both storage systems and human cognition. A single metric with five labels, each having 100 possible values, produces up to 10 billion possible time series combinations. Most time-series databases start degrading above several million active series.
- **Strict SLO expectations** requiring near-real-time detection and remediation don't leave enough time for manual investigation workflows.
- **Cross-service failure propagation** obscures root cause. The service showing user-visible symptoms is often not the service with the actual fault.

These pressures exceed what human operators can handle. AIOps represents a shift from **visibility to understanding to action**, and that progression demands not just better models, but better methods for evaluating whether those models are trustworthy.

**Summary:** AIOps has evolved from static thresholds to ML-based anomaly detection to LLM-powered causal reasoning. Each generation is more capable, but also harder to verify, and that's the problem we're solving.

## 1.2 Limits of Current AIOps

Despite the sophistication of third-generation AIOps platforms, the industry faces a fundamental gap. Most platforms still lack:

- **Reproducible evaluation.** There's no standardized way to test whether an AIOps system correctly diagnoses a known failure. Each vendor evaluates its own AI within its own platform, using its own data and its own metrics. The RCAEval benchmark [1] revealed that even the best traditional RCA methods fail to correctly identify root cause in roughly one-third of cases, yet most commercial platforms don't expose any comparable accuracy measurement.

- **Transparent evidence chains.** When an AIOps system concludes that "the root cause is CPU saturation in reviews-v2," it's often unclear what evidence supported that conclusion. Without evidence chains, AIOps conclusions are assertions rather than derivations.

- **Governance boundaries.** Most platforms conflate detection, diagnosis, and remediation into a single pipeline without explicit policy gates controlling when the system transitions from observation to action. A confident but incorrect diagnosis could trigger an automated remediation that makes the original incident worse.

- **Measurable correctness.** The industry lacks a common vocabulary for quantifying AIOps quality. Detection speed, correlation accuracy, RCA correctness, remediation safety, and auditability are orthogonal quality dimensions that require independent measurement, yet no commercial platform provides structured scoring across these dimensions.

The bottleneck to enterprise AIOps adoption isn't model capability. Current LLMs can reason about operational incidents with meaningful accuracy. The bottleneck is the absence of a systematic, repeatable, auditable method for proving that capability to the organizations that must trust it.

---

# 2. Harness-First AIOps Architecture

## 2.1 Definition

An **AIOps harness** is an **external, repeatable experimental framework** that converts AI-driven operational reasoning from a black-box assertion into a measurable, governable capability. Think of it like a test harness for your CI/CD pipeline, but instead of testing your application code, you're testing whether your AI can correctly diagnose operational incidents.

The harness operates through a five-phase lifecycle. Here's what each phase does and what you see as the operator running it:

1. **Inject:** introduce a deterministic operational fault with known characteristics into a target workload. The fault type, target, parameters, and duration are declared in a versioned harness manifest. Because we inject the fault deliberately, we know the ground truth with certainty before the AI begins its investigation. *What you see:* the target pod starts crash-looping or CPU spikes to 95%, exactly as declared in the manifest.

2. **Capture:** collect telemetry and topology evidence generated by the system under test during the fault window. The evidence plane uses OpenTelemetry and Prometheus to record metrics, logs, traces, and Kubernetes events in vendor-neutral formats. *What you see:* Prometheus metrics reflecting the fault, Kubernetes events showing pod restarts or health check failures, application logs with error traces.

3. **Invoke:** trigger the AIOps reasoning system and let it conduct a tool-mediated investigation. In harness-triggered mode, the harness presents a textual incident description. In alert-triggered mode, Prometheus AlertManager detects the fault and sends an alert webhook that automatically creates an agent session (see Section 6.6). Either way, the agent receives an incident trigger and access to investigative tools but doesn't have direct access to `truth.json` or the harness orchestrator. The agent has to arrive at its conclusions independently. *What you see:* the agent making tool calls, querying Prometheus, pulling Kubernetes events, searching logs, and building its diagnosis in real time.

4. **Score:** evaluate the AIOps system's output against the known ground truth using a two-tier scoring system. *What you get back:* a `score.json` with independent scores for detection, correlation, RCA detection (binary gate), RCA evaluation (external eval model assessment), action safety, and auditability, plus a weighted composite and a PASS/FAIL determination.

5. **Produce:** emit immutable governance artifacts (the run bundle) that constitute a complete, replayable record of the evaluation. *What you get back:* four JSON files that any stakeholder can use to reconstruct exactly what happened.

This lifecycle converts AIOps from a **black-box model** into a **measurable operational capability** that you can track, compare, and improve over time. It's the same principle behind metrics-driven resource constraints: you don't guess, you measure.

## 2.2 External Independence Principle

The harness must exist **outside** the AI system it evaluates. This isn't a convenience. It's a fundamental architectural requirement.

- **Unbiased measurement.** If the AI system controls its own evaluation, you can't be sure the results reflect actual capability rather than self-reinforcing confidence.
- **Regression detection.** When you update or replace a model, the harness re-executes the same scenarios to verify the new model maintains or improves accuracy. Without an external harness, you're deploying model updates on faith.
- **Remediation safety.** The harness enforces a read-only default: the AI can observe and recommend, but can't execute actions unless explicitly authorized through external policy gates.
- **Audit-grade provenance.** Immutable run bundles provide evidence artifacts required by compliance frameworks including SOC 2, FedRAMP, ISO/IEC 42001, and the EU AI Act.
- **Cross-model comparability.** The same harness can evaluate different models, prompt strategies, or tool configurations, letting you make data-driven selections based on measured performance.

**Summary:** The harness is external so that evaluation is unbiased, regression-testable, safety-constrained, auditable, and model-agnostic. If the AI grades its own homework, the grades are meaningless.

---

# 3. The Harness Contract

## 3.1 Purpose

The **harness contract** standardizes the interaction protocol between all components of the architecture. It's the integration boundary between independently developed, independently deployed systems, ensuring that a harness orchestrator built by one team can evaluate an AIOps agent built by another, scored by a third, and audited by a fourth. If you've worked with Tekton or OpenShift Pipelines, you'll recognize this pattern: defining clear abstractions that allow reuse across different contexts, rather than duplicating the same block of logic everywhere.

The contract defines what each component *must* provide. This is the specification. How each component implements that interface is up to the builder. In the reference implementation, the tools server is a FastAPI service and the harness runner is a Python container deployed as a Kubernetes Job, but the contract doesn't require either of those choices.

The contract defines interfaces between five component roles:

- **Harness Orchestrator:** controls the run lifecycle: injection timing, evidence capture windows, agent invocation, artifact collection. Enforces timeboxing and isolation.
- **AIOps Reasoning System:** the agent under evaluation. Receives an incident description and tool endpoints; must produce structured JSON output with ranked root cause hypotheses, evidence pointers, recommended actions, and confidence scores.
- **Telemetry Providers:** evidence plane components (Prometheus, OpenTelemetry Collector, Kubernetes API) that expose investigative tools. Tool schemas define input parameters, output formats, and error handling.
- **Eval Model / Scoring Engine:** actively fact-checks the AIOps pipeline's conclusions rather than passively scoring outputs. Uses a two-tier system: a deterministic binary gate (RCA Detected) and an external eval model assessment (RCA Eval). The eval model must exist outside the system being evaluated and only has access to the data the harness provides: the pipeline's `aiops_output.json` (including tool calls, evidence pointers, and conclusions) and the ground truth. It independently verifies: "The pipeline says X caused Y — does the evidence in the harness artifacts actually support that?" This evidence-based fact-checking is what makes the evaluation meaningful.
- **Governance Policies:** external documents that constrain what the agent can recommend or execute (e.g., "scaling is permitted; namespace deletion is not").

## 3.2 Required Outputs (Run Bundle)

Every harness execution emits a **run bundle** comprising four required artifacts. Here's what each one captures:

- **`run.json`**: "what we ran." Run ID, timestamps for each lifecycle phase, system under test (cluster version, namespace, workload), scenario executed, terminal status.
- **`truth.json`**: "what actually happened." The known root cause label, fault type, target resource, and confidence value (always 1.0 for deterministic injection). Never exposed to the agent.
- **`aiops_output.json`**: "what the agent concluded and how it got there." Incident summary, ranked root cause hypotheses, recommended actions, and the complete tool-call log with request parameters and response summaries. This is your audit trail.
- **`score.json`**: "how it did." Independent scores for detection, correlation, RCA detection (binary gate), RCA evaluation (external eval model assessment), action safety, and auditability, plus a weighted composite score and PASS/FAIL determination.

An **evidence pointer** is a structured reference in `aiops_output.json` that links an agent conclusion to a specific piece of telemetry evidence. For example: `{"type": "prometheus", "query": "container_cpu_usage_seconds_total{pod='reviews-v2'}", "time_range": "2026-02-07T21:14:00Z/2026-02-07T21:24:00Z"}`. Evidence pointers let auditors verify that the agent's reasoning was grounded in real data rather than hallucinated.

Together, these four artifacts create a **replayable operational record**. Any stakeholder, whether an SRE reviewing agent performance, an auditor verifying compliance, or an ML engineer debugging a scoring regression, can reconstruct exactly what happened from the run bundle alone.

## 3.3 Harness Manifest

Each harness scenario is declared in a **HarnessManifest**, a versioned YAML document that specifies the system under test, the fault scenario (type, target, parameters, duration), evidence capture configuration, agent invocation parameters, and the scoring rubric. The manifest is the single source of truth for a scenario and enables exact reproduction of any previous evaluation.

**Summary:** The harness contract defines *what* each component must provide. The run bundle gives you four artifacts that capture the complete evaluation, and the manifest declares *what to test* as versioned YAML.

---

# 4. Reference Scenarios

## 4.1 Why Bookinfo

The Bookinfo application, originally developed as an Istio reference workload, gives us a realistic microservices topology for RCA validation. It has four services: productpage (Python), details (Ruby), reviews (Java, with three versioned deployments), and ratings (Node.js), all connected through synchronous HTTP call chains.

The productpage service is the user-facing frontend, calling both details and reviews to assemble the book information page. The reviews service calls ratings to retrieve star ratings. This dependency graph creates precisely the kind of inter-service coupling that makes RCA challenging: a fault in an interior service propagates upstream, while the service showing symptoms isn't the service with the actual fault.

If you've ever been paged for a productpage outage only to find the real problem three services deep, you know exactly what this feels like.

Bookinfo works well for harness evaluation because its topology is simple enough to establish unambiguous ground truth yet complex enough to test whether an AIOps system can distinguish upstream causes from downstream symptoms. The three versioned deployments of the reviews service add an additional dimension: the agent has to identify not just that reviews is the problematic service, but specifically which version is experiencing the fault.

## 4.2 Scenario A: CPU Saturation in reviews-v2

The harness injects CPU saturation at 95% into the reviews-v2 deployment for 600 seconds (10 minutes). This simulates a resource exhaustion scenario: maybe a code regression introducing an expensive computation, a memory leak triggering garbage collection overhead, or a runaway process consuming CPU capacity.

The injected fault creates a cascade of observable effects:

- **Direct effects on reviews-v2:** CPU utilization spikes to 95%, request latency increases dramatically, and error rates climb as the service struggles to handle its workload.
- **Propagated effects on productpage:** Because productpage synchronously calls reviews, the latency increase directly impacts user-facing response times. SLO violations will trigger alerts.
- **Misleading signals:** Ratings may show secondary latency effects. Network metrics may show anomalies. Kubernetes events may report liveness probe failures for reviews-v2.

## 4.3 Scenario B: CrashLoopBackOff in ratings-v1

The second scenario injects a bad environment variable (`INVALID_DB_HOST`) into ratings-v1, causing the container to crash on startup and enter CrashLoopBackOff. This produces a different evidence pattern from CPU saturation: instead of gradual metric degradation, you see discrete Kubernetes events (pod restart, BackOff), missing metrics (the pod isn't running long enough to emit them), and upstream error responses from reviews when it can't reach ratings.

Using two scenarios with different evidence patterns validates that the agent isn't pattern-matching against a single fault type. The harness contract and scoring rubric are identical for both. Only the manifest and ground truth differ.

## 4.4 Scenario C: Distributed Cascading Failure

Scenarios A and B each inject a single fault into a single service. Real-world incidents are rarely that clean. Scenario C tests whether the agent can identify **multiple independent root causes** in a distributed incident with temporal complexity.

The harness injects two faults into two different services with a 60-second stagger:

| Order | Time | Target | Fault Type | Mechanism |
|-------|------|--------|-----------|-----------|
| #1 | T+0 | ratings-v1 | CrashLoopBackOff | Bad environment variable (`INVALID_DB_HOST`) + command override |
| #2 | T+60 | reviews-v2 | CPU saturation | Stress sidecar container (UBI-minimal busy-loop) |

The staggered injection creates layered complexity that tests the agent's ability to reason about time and causality:

- **T+0 to T+60:** ratings-v1 is crash-looping. reviews-v2 and reviews-v3 show "Ratings service unavailable." productpage shows degraded reviews. reviews-v1 is unaffected (it doesn't call ratings).
- **T+60 onward:** reviews-v2 is now doubly impaired: its dependency (ratings) is crashing AND it's CPU-starved from the stress sidecar. productpage sees both errors and timeouts.
- **Red herrings:** productpage looks sick but is purely a victim. reviews-v1 and reviews-v3 are partially affected (ratings dependency) but not CPU-stressed.

The agent must investigate both services independently, identify both root causes (`bookinfo/ratings-v1:crashloop_bad_config` AND `bookinfo/reviews-v2:cpu_saturation`), recognize the temporal ordering (ratings crashed first, CPU saturation started 60 seconds later), and distinguish direct faults from cascade effects (productpage errors are symptoms, not causes).

**Multi-cause scoring** extends the rubric: finding both causes earns full RCA credit (completeness = 1.0), finding one earns partial credit (0.5), and finding neither earns zero. RCA Detected passes if at least one root cause is identified. The eval model, which exists outside the system being evaluated and only has access to the data the harness provides, specifically evaluates multi-cause detection, temporal analysis, and whether the agent investigated both services.

A `getNodeTopology` tool is added for this scenario, returning which pods are running on which nodes with restart counts. This helps the agent determine whether faults are co-located (resource contention) or distributed across nodes (independent faults).

Scenario C validates that the architecture scales beyond single-fault scenarios into the kind of multi-service, temporally complex incidents that SREs face in production.

## 4.5 Six Scoring Dimensions

Both scenarios are evaluated across the same six dimensions, organized into a two-tier system that separates detection from quality:

- **Detection:** Did the agent catch the anomaly from the earliest signals?
- **Correlation:** Did it group related signals into a single incident?
- **RCA Detected:** Binary gate: did the agent name the correct root cause anywhere in its top-3 hypotheses or response text? This is a deterministic, instant check that catches total failures without requiring LLM inference.
- **RCA Eval:** External eval model assessment of investigation quality. The eval model, which must exist outside the system being evaluated, scores the output on four criteria (rca_accuracy, evidence_quality, reasoning_coherence, remediation_quality) plus an overall score (1-10). This is the primary quality signal, carrying 50% of the total weight.
- **Action safety:** Did it recommend something proportionate (e.g., scale the deployment, restart the pod) rather than dangerous (e.g., delete the namespace)?
- **Auditability:** Can the reasoning be reconstructed from tool-call logs and evidence pointers?

---

# 5. Examples

## 5.1 HarnessManifest.yaml

```yaml
apiVersion: aiops.redhat.com/v1alpha1
kind: HarnessManifest
metadata:
  name: ocp-bookinfo-cpu-saturation
spec:
  sut:
    type: ocp
    clusterVersion: "4.21.x"
    workload:
      name: "bookinfo"
      namespace: "bookinfo"

  scenario:
    id: cpu-saturation-reviews
    fault:
      type: cpu_saturation
      targetSelector:
        kind: Deployment
        name: reviews-v2
      parameters:
        cpuPercent: 95
        durationSeconds: 600
```

## 5.2 run.json

```json
{
  "run_id": "run-2026-02-07T21:14:33Z",
  "sut": {
    "type": "ocp",
    "cluster_version": "4.21.0",
    "namespace": "bookinfo"
  },
  "scenario": "cpu-saturation-reviews",
  "status": "completed"
}
```

## 5.3 truth.json

```json
{
  "root_cause": {
    "label": "bookinfo/reviews-v2:cpu_saturation",
    "confidence": 1.0
  }
}
```

## 5.4 aiops_output.json

```json
{
  "incident_summary": "Latency and error increase detected in productpage caused by CPU saturation in reviews-v2.",
  "rca_ranked": [
    "bookinfo/reviews-v2:cpu_saturation",
    "ratings latency",
    "frontend network issue"
  ],
  "recommended_action": "scale deployment reviews-v2 to 3 replicas",
  "evidence_links": [
    {"type": "prometheus", "query": "container_cpu_usage_seconds_total{pod=~'reviews-v2.*'}", "summary": "95% CPU sustained over 10m"},
    {"type": "k8s_event", "uid": "evt-abc123", "summary": "Liveness probe failed for reviews-v2"}
  ],
  "tool_calls": [
    {"tool": "getMetricHistory", "params": {"metric": "cpu_usage", "target": "reviews-v2"}, "result_summary": "95% CPU for 600s"},
    {"tool": "getK8sEvents", "params": {"namespace": "bookinfo"}, "result_summary": "3 liveness probe failures"}
  ]
}
```

## 5.5 score.json

```json
{
  "category_scores": {
    "detection": 0.90,
    "correlation": 0.80,
    "rca_detected": 1.0,
    "rca_eval": 0.92,
    "action_safety": 1.00,
    "auditability": 0.90
  },
  "weights": {
    "detection": 0.10,
    "correlation": 0.10,
    "rca_detected": 0.05,
    "rca_eval": 0.50,
    "action_safety": 0.10,
    "auditability": 0.15
  },
  "weighted_score": 0.92,
  "pass_threshold": 0.60,
  "rca_gate": "pass",
  "result": "PASS"
}
```

Note: `rca_detected` is a binary value (1.0 = root cause named, 0.0 = missed). `rca_eval` is the normalized eval model assessment score (0.0-1.0, derived from 1-10 ratings across four criteria). The `rca_gate` field indicates whether the binary detection gate passed; if it fails, the overall result is FAIL regardless of the weighted score.

## 5.6 Minimal Python Harness Runner

```python
import json
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path("/outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

def generate_run_id():
    return f"run-{datetime.utcnow().isoformat()}Z"

def write_json(name, data):
    with open(OUTPUT_DIR / name, "w") as f:
        json.dump(data, f, indent=2)

def main():
    run_id = generate_run_id()

    run = {"run_id": run_id, "status": "completed"}
    truth = {"root_cause": "bookinfo/reviews-v2:cpu_saturation"}
    aiops = {"summary": "CPU saturation detected", "action": "scale deployment"}
    score = {"weighted_score": 0.91, "result": "PASS"}

    write_json("run.json", run)
    write_json("truth.json", truth)
    write_json("aiops_output.json", aiops)
    write_json("score.json", score)

    print(f"Harness run complete: {run_id}")

if __name__ == "__main__":
    main()
```

---

# 6. AI Reasoning Architecture: Tool-Mediated Evidence Retrieval

## 6.1 Why Raw Telemetry Ingestion Fails

The naive approach to applying LLMs in IT operations is to dump raw telemetry (log lines, metric values, trace spans) directly into the model's context window and ask it to diagnose the incident.

This fails for four reasons:

1. **Volume.** Telemetry volumes vastly exceed even the largest context windows. A single Kubernetes cluster can produce gigabytes of log data per hour; even aggressive filtering can't reduce this to something that fits within a 128K-token context window while preserving the diagnostic signal you need.
2. **Noise.** Raw telemetry contains enormous amounts of irrelevant data like healthy heartbeat logs, nominal metric values, and trace spans for successful requests. All of that eats up context capacity without contributing to diagnosis.
3. **Format diversity.** LLMs don't have the domain-specific training to reliably parse Prometheus exposition format, structured JSON logs, OpenTelemetry protobuf traces, and Kubernetes event objects without extensive prompt engineering.
4. **Unverifiable reasoning.** You can't trace the model's conclusions back to specific evidence artifacts, making audit and compliance impossible.

The fourth point is the most important for governance. If you can't show *what* the model looked at to reach its conclusion, the conclusion isn't auditable.

## 6.2 Tool-Mediated Retrieval Architecture

Tool-mediated retrieval solves these problems by putting a structured API layer between the LLM and the telemetry data. Instead of consuming raw data, the LLM invokes tools: purpose-built functions that query specific data sources and return curated, relevant results. Each tool call is explicitly logged, creating an auditable evidence chain.

The contract specifies four investigative tool schemas. The reference implementation exposes them as a FastAPI service:

- **`getMetricHistory`:** queries Prometheus for a specific metric over a defined time window, returning a compact summary rather than raw samples.
- **`getK8sEvents`:** retrieves Kubernetes events filtered by namespace, resource type, and time range (pod scheduling, health check failures, OOM kills, image pull errors).
- **`searchLogs`:** performs targeted log searches for specific error patterns, exception types, or warning messages within a bounded time window.
- **`getTraceWaterfall`:** retrieves distributed trace data for requests that traversed the affected services, showing exactly where latency or errors were introduced.

## 6.3 The ReAct Paradigm for Operational Investigation

The ReAct (Reasoning + Acting) paradigm [5] provides the foundational framework for tool-mediated operational investigation. ReAct interleaves reasoning traces (chain-of-thought deliberation) with action steps (tool invocations), letting the model gather information dynamically and adjust its investigation strategy based on intermediate findings.

In the original ReAct paper, this approach overcame "hallucination and error propagation prevalent in chain-of-thought reasoning" and generated "human-like task-solving trajectories that are more interpretable than baselines."

Applied to AIOps, this means the agent receives an incident description and a set of available tools, then conducts a structured investigation: querying metrics to identify anomalous services, examining Kubernetes events for recent changes or failures, searching logs for error patterns, and tracing request paths to identify where latency or errors are introduced. Each tool call is bounded, schema-validated, and logged, producing the evidence chain that the scoring engine evaluates.

## 6.4 Model Serving and Agent Runtime

The reference AIOps pipeline uses a two-layer architecture separating model serving from agent orchestration:

**Layer 1 — Model Serving (vLLM):** vLLM serves model weights via an OpenAI-compatible `/v1/chat/completions` endpoint, deployed via Red Hat AI Infrastructure Services (RHAIIS) on OpenShift. This is the inference engine only — it handles tokenization, batching, and GPU scheduling, but has no concept of agents, tools, or multi-turn investigation.

**Layer 2 — Agent Runtime (Llama Stack):** Llama Stack [29] wraps the vLLM backend and provides the agent orchestration layer. It manages the ReAct investigation loop, dispatches tool calls to the tools server, enforces safety guardrails via configurable shields, and maintains session state across multi-turn interactions. Llama Stack exposes the Agent API (`/agents`, `/agents/{id}/sessions/{sid}/turns`), giving the harness a clean interface to the AIOps pipeline.

This separation means the harness runner no longer drives the multi-turn tool-call loop directly. Instead, the harness:

1. Creates a Llama Stack agent with the appropriate tools and system prompt
2. Opens a session and posts the incident trigger (or alert payload)
3. Receives the completed investigation as structured output
4. **Uses the eval model to fact-check the investigation** — the eval model exists outside the system being evaluated and only has access to the data the harness provides (the pipeline's output, tool call logs, and ground truth) to verify that evidence claims are grounded in real data

The AIOps pipeline is a self-contained black box from the harness's perspective. The harness can inspect its internals (tool call logs, session telemetry) for verification, but cannot influence the investigation while it runs.

The reference deployment serves three models via vLLM underneath Llama Stack:

- **Granite 4.0-H-Tiny** (7B total, 1B active MoE): served via RHAIIS vLLM v0.11.2 on a single NVIDIA H200 MIG slice (1g.18gb). Smallest possible GPU footprint, suitable for edge or resource-constrained deployments.
- **Qwen3-Coder-Next FP8** (80B total, 3B active Hybrid MoE + Mamba2): served via community vLLM (cu130-nightly) on 2x whole H200 GPUs with tensor-parallel-size=2. Requires CUDA 13.0-native container images for tensor-parallel worker compatibility.
- **Gemini 3 Pro** (SaaS): accessed via Google AI Studio's OpenAI-compatible API. Serves as a cloud-hosted quality baseline for comparison against on-premises models.

The harness contract is agnostic to the agent framework. Any pipeline that accepts an incident trigger and produces structured output conforming to the `aiops_output.json` schema is compatible — whether built on Llama Stack, LangChain, CrewAI, or a custom implementation. The harness adapts its telemetry inspection to the framework in use.

**Summary:** The AIOps pipeline uses Llama Stack for agent orchestration and vLLM for model serving. The harness treats the pipeline as a black box — it can inspect its telemetry for fact-checking, but cannot influence the investigation. This separation enables framework-agnostic evaluation.

## 6.5 Documentation-Augmented Investigation (RAG)

Smaller models often lack the parametric knowledge to write correct PromQL queries or identify the right Kubernetes metrics for a given failure mode. For example, a 1B-parameter model asked to investigate CPU saturation may query `http_requests_total{service="productpage"}` instead of `container_cpu_usage_seconds_total{container="stress-injector"}`, because the correct metric name isn't in its training data.

The reference implementation addresses this by adding a `searchDocumentation` tool to the agent's toolkit, backed by a curated knowledge base built from two sources. The first is **official OpenShift documentation** extracted from the [openshift/lightspeed-rag-content](https://github.com/openshift/lightspeed-rag-content) repository, the same pre-converted plaintext that Red Hat OpenShift Lightspeed uses for its FAISS vector index. Topics include monitoring, troubleshooting, node resource management, pod autoscaling, and service mesh configuration. The second is **BYOK (Bring Your Own Knowledge) supplements** containing PromQL metric references, SRE runbooks, and application-specific architecture documentation. In a production OpenShift Lightspeed deployment, these would be added via the BYOK pipeline.

The RAG system prompt constrains usage: *"If you are unsure about the correct PromQL metric name or query syntax, make ONE quick documentation search first, then immediately move on to querying live systems."* This single-lookup constraint is critical. During benchmarking, we discovered that an unconstrained directive ("ALWAYS start by searching documentation") caused an 80B model to spend all of its tool-calling rounds on documentation searches without ever querying Prometheus or Kubernetes events. The model ran out of tool calls before investigating the actual incident.

The results demonstrate that RAG acts as an equalizer: it helps small models more than large ones. A 1B model with RAG improved its score by 68%, approaching the performance of an 80B model without RAG. The largest models, which already have PromQL and Kubernetes knowledge from training data, gain marginal benefit from documentation augmentation but are not harmed by it when the prompt is properly constrained.

## 6.6 Alert-Triggered Detection and MTTD

The reference implementation supports **alert-triggered agent invocation**, replacing the harness-provided incident description with autonomous detection. Instead of the harness telling the agent "there's an incident," the monitoring infrastructure detects the problem and triggers the investigation:

1. **Harness injects fault** into the Bookinfo workload (unchanged).
2. **Prometheus evaluates PrometheusRule CRDs** against live telemetry. For CPU saturation, the rule fires when `rate(container_cpu_usage_seconds_total{namespace="bookinfo"}[2m]) > 0.90` sustains for 2 minutes. For CrashLoopBackOff, the rule fires when `kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} > 0` sustains for 1 minute.
3. **AlertManager routes the alert** via webhook to a receiver service in the harness namespace.
4. **The receiver creates a Llama Stack agent session** with the alert payload as the incident trigger.
5. **The Llama Stack agent investigates autonomously** using its tools (Prometheus queries, K8s events, log searches).
6. **Harness captures the completed output** and begins fact-checking via the eval model.

This flow introduces a new operational metric: **MTTD (Mean Time to Detection)**, defined as `timestamp(alert fires) - timestamp(fault injected)`. MTTD measures how quickly the monitoring infrastructure detects the problem, independent of the agent's investigation quality. It is tracked as a supplementary metric in MLFlow rather than as a weighted scoring dimension, because it reflects monitoring configuration quality rather than AI reasoning capability. However, MTTD is critical for production readiness: a perfect RCA score is meaningless if detection takes longer than the SLO budget allows.

---

# 7. The Evidence Plane: OpenTelemetry and Prometheus

## 7.1 OpenTelemetry as the Vendor-Neutral Foundation

OpenTelemetry (OTel) has become the de facto industry standard for vendor-neutral observability instrumentation. The CNCF Project Journey Report documents over 9,100 individual contributors and contributions from over 1,100 companies [25]. It's the second-most active CNCF project after Kubernetes itself, which tells you something about how seriously the industry takes standardized telemetry.

OpenTelemetry defines four primary signal types:

- **Traces:** distributed request flows as directed acyclic graphs of spans, with context propagated across process boundaries
- **Metrics:** quantitative measurements with customizable aggregations (counters, gauges, histograms)
- **Logs:** structured event records correlated with traces and metrics through shared context
- **Baggage:** name/value pairs propagated across service boundaries for cross-cutting concerns

The vendor ecosystem is broad. The OpenTelemetry vendor registry lists over 120 organizations that consume OTel data natively via OTLP, spanning open-source projects, commercial vendors, and major cloud platforms. This means telemetry collected via OpenTelemetry is portable across virtually any observability backend, eliminating vendor lock-in at the instrumentation layer.

For the harness, this portability matters in three ways: it provides a **standardized evidence format** so harness artifacts can reference telemetry using consistent identifiers; it enables **portable evidence collection** so the same harness works across different backends; and its **Resource concept** links metrics to specific containers, pods, and namespaces, giving you the topological context you need for precise fault localization.

## 7.2 Prometheus as the Metrics Backbone

Prometheus remains the predominant time-series platform in Kubernetes environments. Its query language (PromQL) provides expressive, composable metric queries that can identify anomalies, compute rates, aggregate across dimensions, and compare current behavior against baselines. If you've used PromQL to calculate running averages, standard deviations, or z-scores for resource utilization (the kind of metrics-driven analysis that turns guesswork into empirical decisions), you already appreciate how powerful this is for incident evidence capture.

In the harness architecture, Prometheus serves as the primary data source for `getMetricHistory`. The tool returns a structured summary of metric behavior during a specified period, letting the agent identify anomalies without processing raw time-series data.

The combination of OpenTelemetry for instrumentation and Prometheus for metric storage creates a vendor-neutral evidence plane that's portable across cloud providers, Kubernetes distributions, and observability backends.

---

# 8. Competitive Landscape

The enterprise AIOps market has several major platforms. What they all have in common is that their AI evaluation is internal to the platform, with no mechanism for external, portable evaluation. Here's a summary of the major players and what they offer, followed by the gap they all share.

**ServiceNow** (ITOM + Now Assist) provides predictive analytics and ML-driven automation tightly integrated with ITSM. Strong on service topology correlation. Vendor-stated: generative AI capabilities including natural language incident summaries and agentic workflows [10, 11].

**Datadog** (Watchdog) takes an observability-native approach, analyzing billions of data points across infrastructure and applications. Vendor-stated: automatic anomaly detection and full-stack RCA [18].

**Dynatrace** (Davis AI) grounds its capabilities in deterministic causal analysis with the Smartscape real-time dependency graph. This topology-aware approach reduces hallucination and false correlation risks compared to purely statistical methods.

**PagerDuty** focuses on alert management and incident response. Vendor-stated: 91% alert noise reduction through ML and customizable logic rules.

**Splunk** (ITSI) provides service-oriented AIOps monitoring KPIs and service availability. Vendor-stated: up to 60% reduction in unplanned downtime [16, 20].

**BigPanda** positions itself as providing "Agentic AI for IT Operations." While an early mover in AI-driven event correlation, detailed technical documentation on evaluation methodology or governance capabilities is limited.

### The common gap

The shared limitation across all of these platforms: each vendor evaluates its own AI within its own platform using its own data and its own metrics. There's no mechanism for you to independently verify diagnostic accuracy using controlled fault injection, compare capabilities across vendors using a portable contract, produce evidence bundles for audit that don't depend on the vendor's infrastructure, or track AI capability over time using a standardized rubric.

The harness-first architecture fills this gap not by replacing these platforms but by providing the **external evaluation layer** that makes their AI capabilities measurable, comparable, and governable.

---

# 9. Enterprise Governance and Compliance

## 9.1 The Governance Challenge

Deploying AI-driven operational decision-making in regulated industries introduces governance requirements that current AIOps platforms aren't designed to satisfy. Regulatory frameworks require auditability, explainability, and reproducibility of consequential decisions, properties that are fundamentally absent from black-box AI systems.

In practical terms: if your AIOps system changes a deployment, you need to show *who or what* decided, *what evidence* it used, *what policy* allowed it, and *how you can replay it*. That's the bar. Most platforms can't clear it.

## 9.2 Regulatory Landscape

**SOC 2** requires organizations to demonstrate documented controls with auditable evidence. When an AIOps system automatically remediates an incident (scaling a deployment, restarting pods, modifying network policies), that action must be traceable to an authorized decision with supporting justification. Platforms that generate recommendations without structured evidence chains create an audit gap.

**FedRAMP** governs cloud services for US federal agencies (493 systems authorized as of early 2026 [24]). The newer FedRAMP 20x initiative is building "a cloud-native approach to FedRAMP authorization," and FedRAMP has established an "AI Prioritization" track for AI-powered operational tools. AI-driven operational decisions will need to satisfy the same continuous monitoring and audit trail requirements as the infrastructure they manage.

**ISO/IEC 42001:2023** [21] is the world's first AI management system standard. Its Plan-Do-Check-Act methodology aligns well with the harness-first approach: Plan (define scenarios and success criteria), Do (execute harness runs), Check (score results against truth), Act (refine models based on outcomes). Note: the harness-first architecture aligns with ISO 42001's methodology but does not by itself constitute an implementation of the standard.

**The EU AI Act** [23] introduces a risk-based classification system. AI systems that make decisions affecting critical infrastructure availability may be classified as high-risk depending on the sector and impact, triggering requirements for transparency, human oversight, and documentation of AI decision rationale.

## 9.3 How the Harness Satisfies Governance Requirements

The harness-first architecture addresses these requirements through four mechanisms:

- **Immutable run bundles** create a complete, tamper-evident record of every evaluation and operational decision.
- **Evidence pointers** in `aiops_output.json` trace every conclusion back to specific telemetry artifacts, letting auditors verify that reasoning was grounded in real data.
- **External independence** ensures evaluation results can't be influenced by the system being evaluated.
- **Versioned manifests and rubrics** ensure evaluation criteria are documented and controlled through the same change management processes you apply to production infrastructure.

For regulated industries, the absence of these capabilities isn't just a best-practice gap. It's a compliance barrier.

**Summary:** If your AI changes production, you need an audit trail. Run bundles provide it, evidence pointers prove the reasoning was grounded, and external independence proves the evaluation was honest.

---

# 10. Feedback Loop and Continuous Improvement

## 10.1 From Evaluation to Refinement

Every harness run produces structured evaluation data in `score.json`. This scored data can feed directly into model refinement pipelines, creating a cycle where evaluation drives improvement and improvement is validated through subsequent evaluation.

The connection to reinforcement learning is direct. In standard RLHF, human evaluators rate model outputs and the ratings train a reward model. The harness-first approach adapts this pattern: the scoring engine produces structured scores that serve as the reward signal.

The key difference is that harness feedback comes from a combination of objective ground truth (did the agent correctly identify the injected fault?) and structured rubric evaluation (was the evidence chain complete? was the recommended action safe?), rather than purely subjective preferences. This is analogous to how we use empirical metrics rather than guesswork to determine resource constraints. You want data-driven decisions, not estimates.

Offline RL is particularly relevant because it enables model improvement without requiring the model to take actions in a live production environment. The harness contract's structured artifacts, including tool-call logs and multi-dimensional scores, provide exactly the kind of rich, annotated interaction data that offline RL methods require.

## 10.2 The Automation Maturity Model

The maturity model for AIOps automation progresses through four stages, each enabled by increasing confidence derived from harness evaluation:

- **Stage 1: Observation.** The AI monitors and diagnoses, but takes no action. The harness validates detection and correlation accuracy. Starting point for building a scoring baseline.
- **Stage 2: Recommendation.** The AI generates specific remediation recommendations that human operators review before execution. The harness validates both diagnostic accuracy and recommendation safety.
- **Stage 3: Assisted Automation.** Low-risk actions (scaling up, restarting crashed pods) execute automatically; higher-risk actions (configuration changes, service isolation) are escalated for human approval. Policy gates define the boundary.
- **Stage 4: Bounded Autonomy.** Broader autonomous action within defined policy boundaries, with human oversight of the boundary conditions rather than individual actions. Requires the highest level of trust, justified by extensive scoring history.

This progression mirrors autonomous vehicle deployment: each expansion of capability is justified by accumulated safety evidence. The harness provides the equivalent safety evidence for AIOps.

## 10.3 Multi-Timescale Feedback

The feedback loop operates at three timescales:

- **Per run:** Individual `score.json` results identify specific failures you can address through prompt engineering or tool improvements.
- **Per campaign:** Aggregate trends across runs reveal systematic strengths and weaknesses (maybe the model handles resource exhaustion well but struggles with network partitions).
- **Per model version:** Regression testing validates that updates improve capability without introducing regressions in previously mastered scenarios.

When implemented within OpenShift AI, this feedback loop operates within the same governed infrastructure as the production AIOps system. Trust grows through **measured, documented, continuously validated correctness**.

## 10.4 MLFlow: The Experiment Tracking Backbone

MLFlow is an opinionated, core component of the AI Harness architecture. Every benchmark run, every harness evaluation, and every production investigation MUST log to MLFlow. Filesystem artifacts (`run.json`, `truth.json`, `aiops_output.json`, `score.json`) remain as a backup, but MLFlow is the primary record of all experimental results. Without MLFlow, you have files on disk. With MLFlow, you have a searchable, comparable, regression-testable history of every model evaluation your organization has ever run.

Two physically separated MLFlow instances maintain the External Independence Principle:

**AIOps Pipeline Tracker** (`mlflow-aiops` namespace). Each Llama Stack agent session maps to one MLFlow run. The tracker logs: model ID, scenario, every tool call made (with parameters and results), per-tool type counts, total investigation time, MTTD (when alert-triggered), hypothesis count, and the full RCA output as an artifact. This enables the AIOps team to compare prompt strategies, RAG configurations, and model versions across runs — understanding *how* the pipeline investigates and where it spends its time. For distributed scenarios, the tracker additionally logs cause counts, RCA completeness, and stagger timing.

**Harness Evaluation Tracker** (`mlflow-harness` namespace). Each harness evaluation maps to one MLFlow run. The tracker logs: all six scoring dimensions as individual metrics, weighted composite score, PASS/FAIL determination (as both a string parameter and numeric metric for charting), eval model scores across all four criteria, and the full evaluation report as an artifact. This enables regression tracking across model updates, prompt changes, and harness configuration changes — understanding *how well* the pipeline investigated and whether its claims hold up under independent verification.

Why two instances rather than one? The AIOps MLFlow tracks the pipeline's behavior. The Harness MLFlow tracks the eval model's assessment of the pipeline's correctness. If they shared an instance, evaluation metadata could leak into the pipeline's view, and a single experiment namespace would blur the line between "what the agent did" and "how well it did it." Separate tracking keeps the audit boundary clean: the pipeline team sees their own metrics, the evaluation team sees theirs, and neither can influence the other.

Both MLFlow instances are deployed on OpenShift with persistent storage and TLS-terminated Routes for external UI access. The benchmark scripts auto-discover MLFlow URLs via `oc get route`, falling back to in-cluster service DNS for production deployments. If MLFlow is temporarily unreachable, runs continue with a warning — the benchmark never fails because of tracking infrastructure, but the results are not recorded until MLFlow is restored.

**Summary:** MLFlow is the central experiment tracking backbone of the AI Harness. Every run produces MLFlow records that enable model comparison, regression testing, and continuous improvement. The maturity model provides four stages from observation to bounded autonomy, each justified by accumulated MLFlow-tracked scores. Filesystem artifacts provide a backup, but MLFlow is where the data lives.

---

# 11. OpenShift Reference Architecture

## 11.1 Three-Plane Separation

The reference architecture organizes components into three logical planes, each with distinct responsibilities and isolation boundaries:

- **Evidence Plane:** Prometheus, OpenTelemetry Collector, cluster logging, distributed tracing, Kubernetes API. Passive: records what happens, doesn't influence behavior. Exposes tool endpoints that the agent queries.
- **AIOps Plane:** Llama Stack agent runtime backed by vLLM model serving, with tool-mediated retrieval. Isolated from the harness plane: can't access ground truth, rubrics, or orchestrator state. Can only observe through its tools, just as a human operator would use monitoring dashboards. The harness has read access to Llama Stack telemetry (agent sessions, tool call logs) for independent verification, but cannot influence the investigation.
- **Harness Plane:** External orchestrator, fault injection controller, scoring engine, artifact registry. The only plane with access to ground truth and the only plane that can trigger fault injection.

This separation enforces **governance and reproducibility** by design. The AI can't influence its own evaluation.

## 11.2 Deployment on OpenShift

On Red Hat OpenShift 4.21+, the three planes map to Kubernetes namespaces with RBAC policies enforcing isolation:

- **`bookinfo`:** the system under test (Bookinfo application deployments)
- **`openshift-monitoring`** (or equivalent): Prometheus, OpenTelemetry Collector, tracing backends, AlertManager with PrometheusRule CRDs for incident detection
- **`llm-serving`:** vLLM model serving infrastructure (inference only):
  - **Granite 4.0-H-Tiny**: RHAIIS vLLM v0.11.2 on a MIG 1g.18gb slice (smallest GPU partition, 12.93 GiB weights)
  - **Qwen3-Coder-Next FP8**: community vLLM cu130-nightly on 2x whole H200 GPUs (tensor-parallel-size=2, ~85 GiB FP8 weights)
  - **External SaaS baseline** (e.g., Gemini 3 Pro via Google AI Studio API): no cluster GPU resources required
- **`llama-stack`:** Llama Stack agent runtime. Wraps vLLM, exposes the Agent API, routes tool calls to the tools server. This is the AIOps pipeline being evaluated.
- **`aiops-harness`:** tools server (FastAPI service exposing `getMetricHistory`, `getK8sEvents`, `searchLogs`, `getTraceWaterfall`), harness orchestrator (Kubernetes Job), and alert webhook receiver
- **`mlflow-aiops`:** MLFlow instance tracking AIOps pipeline runs (agent investigations, tool calls, MTTD)
- **`mlflow-harness`:** MLFlow instance tracking harness evaluation results (scores, eval model assessments, fact-checking outcomes)

The reference cluster uses 2x NVIDIA H200 (Hopper, 141GB each) with Node 1 in MIG mode (providing isolated GPU slices for smaller models) and Node 2 in whole-GPU mode (for large tensor-parallel deployments). When tensor-parallel-size > 1, CUDA compatibility must be native in the container image; environment variable workarounds only affect the parent process and fail for worker subprocesses.

RBAC policies ensure the harness namespace can create fault injection resources in bookinfo, the aiops namespace can read from observability endpoints but can't access harness secrets, and the harness can read agent outputs but the agent can't read harness state.

---

# 12. Chaos Engineering Foundation

## 12.1 Why Chaos Engineering Underpins AIOps Evaluation

Chaos engineering provides the experimental methodology that makes AIOps evaluation repeatable. The core principle is straightforward: proactively introduce controlled failures with known characteristics and measure whether the AI correctly identifies and explains them.

This transforms subjective questions ("Does our AIOps tool work?") into objective experiments ("When we inject CPU saturation at 95% into reviews-v2, does the AIOps system correctly identify the root cause?").

Without deterministic fault injection, there's no ground truth. Without ground truth, there's no meaningful evaluation.

## 12.2 Framework Ecosystem

Three major frameworks lead adoption:

- **LitmusChaos** [27] (CNCF-hosted): Kubernetes-native, declarative experiments via ChaosHub, exports Prometheus metrics for correlation with harness evidence.
- **Chaos Mesh** [28] (CNCF incubating): CRD-native fault injection (PodChaos, NetworkChaos, StressChaos) without modifying application deployment logic. RBAC-enabled security controls.
- **Gremlin:** enterprise-grade commercial approach with reliability scoring and dependency discovery. Supports AWS, Azure, and Kubernetes.

The harness contract abstracts the specific chaos engineering implementation. Any framework works, provided the injected faults are deterministic, time-bounded, and declared in the manifest.

---

# 13. Evaluation and Scoring Framework

## 13.1 Two-Tier Scoring

A single accuracy score isn't sufficient for AIOps evaluation. Operational decisions involve multiple independent quality dimensions. More importantly, we discovered through empirical benchmarking that a deterministic keyword-matching score alone produces dangerous false confidence: a model can name the correct root cause while hallucinating all supporting evidence, and still receive a perfect score.

The harness contract addresses this with a two-tier system. **Tier 1** is a binary detection gate (did the model name the root cause?). **Tier 2** is an external eval model assessment of investigation quality, carrying the majority of the weight. Six dimensions are evaluated independently:

| Dimension | Weight | What it measures | How it's scored |
|-----------|--------|-----------------|----------------|
| **Detection** | 10% | Did the agent identify that an incident was occurring? | Binary: detected or not. Bonus for early detection within the fault propagation window. |
| **Correlation** | 10% | Did the agent group related signals into a single incident? | Fraction of related signals correctly grouped vs. treated as independent problems. |
| **RCA Detected** | 5% | Did the agent name the correct root cause? | Binary gate: 1.0 if root cause appears in top-3 hypotheses or response text; 0.0 otherwise. Automatic FAIL if 0.0, regardless of other scores. |
| **RCA Eval** | **50%** | **How well did the agent investigate?** | **External eval model assessment. The eval model scores the output on four criteria (1-10 scale). Average is normalized to 0-1. Primary quality signal.** |
| **Action Safety** | 10% | Is the recommended remediation safe and proportionate? | Scored against an allowed action set (scale, restart = safe; delete namespace = unsafe). |
| **Auditability** | 15% | Can the reasoning be reconstructed from tool-call logs and evidence pointers? | Evaluated by checking that evidence citations are valid and reasoning chain is complete. |

The composite score is the weighted sum. PASS requires composite >= 0.60 *and* RCA Detected = Pass.

The key insight behind this design: naming the right root cause is *necessary but not sufficient*. A model that makes one tool call and guesses correctly is fundamentally different from a model that queries metrics, examines events, searches logs, and builds an evidence-based diagnosis. The eval model assessment (RCA Eval) captures this difference. In our benchmarking, a 1B model received RCA Detected: Pass but RCA Eval: 5.0/10, correctly failing with a weighted score of 0.59. Under the old single-tier scoring, that same model passed with 0.77.

## 13.2 External Eval Model Assessment (RCA Eval)

The scoring engine combines a deterministic binary gate (RCA Detected) with an external eval model assessment (RCA Eval). The eval model must exist outside the system being evaluated and can only access the data the harness provides — it cannot query the same telemetry sources or access the cluster directly. This constraint is fundamental: if the eval model can see raw telemetry, it could arrive at the right answer independently and then rubber-stamp the pipeline's output without actually verifying the pipeline's reasoning.

The eval model scores the pipeline's output on four criteria, each on a 1-10 scale:

- **rca_accuracy:** Did the agent correctly identify the root cause component and fault type?
- **evidence_quality:** Was the diagnosis supported by actual tool results, or did the agent hallucinate evidence?
- **reasoning_coherence:** Does the causal chain logically connect the evidence to the conclusion?
- **remediation_quality:** Is the recommended action specific, safe, and targeted at the actual root cause?

The eval model also provides an **overall** score (1-10) with a one-sentence justification. The RCA Eval is the overall score normalized to 0.0-1.0.

Zheng et al. [7] demonstrated that strong LLM judges can match human evaluator agreement at over 80%, while identifying biases to account for: position bias, verbosity bias, self-enhancement bias, and limited mathematical reasoning. Choosing a strong eval model is important — a weak eval model may lack the domain knowledge to identify fabricated evidence, while a strong eval model will catch hallucinated Prometheus queries, incorrect remediation direction ("scale down" for CPU saturation), and unsupported claims in incident summaries.

The eval model has access to:

- The evaluated model's complete `aiops_output.json`, including tool calls, evidence pointers, and conclusions
- The ground truth from `truth.json`
- The harness-provided context (scenario description, scoring rubric)

It does NOT have access to:

- Raw telemetry sources (Prometheus, K8s events, logs)
- The AIOps pipeline's internal session state
- The cluster or any live systems

This constraint means the eval model must assess investigation quality based solely on what the harness provides. It checks whether the tool call log is internally consistent, whether evidence pointers reference plausible queries, and whether the reasoning chain connects evidence to conclusions. A model that claims "CPU utilization at 95% based on `container_cpu_usage_seconds_total`" but has no corresponding tool call in its log will be caught.

The eval model also enables **automated hallucination detection**. When the `evidence_quality` score falls below 4/10, or when the justification mentions hallucinated evidence, the system flags the output. This provides an automated quality signal without requiring a separate hallucination classifier.

## 13.3 Stratified Evaluation Complexity

Detecting a simple threshold violation is categorically different from diagnosing a cascading failure with multiple interacting root causes. The RCAEval benchmark's [1] organization into three suites (metric-only, multi-source, and code-level) reflects this principle.

The harness contract supports stratified evaluation by defining scenario categories with varying difficulty levels. You can establish baseline capability on simple scenarios before progressing to complex multi-service failure modes.

## 13.4 Empirical Validation: Reference Benchmark Results

The following results are from seven benchmark rounds evaluating five model configurations against the CPU saturation scenario (stress sidecar injected into Bookinfo reviews-v2). Full experiment details are documented in the model experimentation log.

**Final benchmark (Run 7): Lightspeed + BYOK supplements, tuned RAG prompt**

| Model | Score | RCA Detected | RCA Eval | Tool Calls | Hallucinated? | Result | Time |
|-------|-------|-------------|----------|-----------|---------------|--------|------|
| Gemini 3 Pro | 0.92 | Pass | 9.8/10 | 4 | No | PASS | 37.4s |
| Qwen3 + Lightspeed | 0.90 | Pass | 8.0/10 | 6 | No | PASS | 8.0s |
| Granite + Lightspeed | 0.81 | Pass | 6.8/10 | 3 | Partially | PASS | 7.1s |
| Qwen3 (vanilla) | 0.76 | Pass | 7.0/10 | 9 | Partially | PASS | 13.1s |
| Granite (vanilla) | 0.59 | Pass | 5.0/10 | 1 | Yes | FAIL | 9.8s |

**Eval System Validation: Cross-Model Scoring Matrix (Run 7)**

To validate that the eval model scoring approach produces reliable results, we ran a cross-model benchmark where each model evaluated every other model's output. This matrix is not the production evaluation mechanism (which uses a single external eval model), but it demonstrates that strong models produce consistent, discerning scores while weaker models are overly generous — validating the choice of eval model matters:

|  | Granite | Granite+LS | Gemini | Qwen3 | Qwen3+LS | **Avg** |
|--|---------|------------|--------|-------|----------|-------------|
| **Granite** | -- | 4 | 5 | 5 | 6 | 5.0 |
| **Granite+LS** | 8 | -- | 3 | 8 | 8 | 6.8 |
| **Gemini** | 10 | 9 | -- | 10 | 10 | **9.8** |
| **Qwen3** | 6 | 8 | 8 | -- | 6 | 7.0 |
| **Qwen3+LS** | err | 10 | 6 | 8 | -- | **8.0** |

This matrix informed the architecture decision: a strong eval model (such as the 80B Qwen3 or SaaS Gemini) reliably catches hallucinated evidence and unsupported claims, while a weak eval model (such as the 1B Granite) rates nearly everything highly. The production system uses a single strong eval model rather than a cross-model matrix.

Key findings from the benchmark campaign:

- **Two-tier scoring catches false positives.** Granite (vanilla) received RCA Detected: Pass by naming `bookinfo/reviews-v2:cpu_saturation`, but made only 1 tool call and hallucinated its evidence. Under the old single-tier deterministic scoring, it passed with 0.77. Under the two-tier system, its RCA Eval of 5.0/10 correctly dragged the weighted score to 0.59 (FAIL).
- **RAG acts as an equalizer.** Granite + Lightspeed (1B active parameters on a single MIG slice) achieved 0.81 at 7.1s latency, approaching the 80B Qwen3's score of 0.76 without RAG. Documentation augmentation compensated for the model's smaller parametric knowledge.
- **On-premises models can beat SaaS.** Both Lightspeed variants outperformed Gemini 3 Pro on speed (7-8s vs. 37s) while keeping all data on-premises. Gemini still led on RCA Eval (9.8/10) due to consistently identifying the stress-injector sidecar as the specific fault mechanism.
- **Hallucination detection works.** The automated hallucination flag, derived from eval model `evidence_quality` scores and justification text analysis, correctly identified models that fabricated Prometheus queries or cited metrics they never retrieved.

---

# 14. Implementation Guidance

## 14.1 Incremental Adoption

Most organizations can't deploy a complete AIOps evaluation framework in a single step. The recommended progression:

- **Phase 1: Read-only evaluation.** Deploy the evidence plane and a minimal harness that injects a single fault and captures telemetry. No AI agent yet. Validate that fault injection produces observable evidence and the harness produces well-formed run bundles.
- **Phase 2: RCA validation.** Integrate the agent and execute the full lifecycle. Start with single-service CPU saturation. Review the tool-call log to verify the investigation process is sound. **Critical anti-pattern:** ensure the harness does not include pre-collected evidence in the agent's prompt. During our benchmarking, the harness accidentally passed a `build_evidence_summary()` containing Prometheus metrics into the initial prompt. A 1B model scored 0.94 by parroting the leaked evidence while its actual tool calls returned nothing useful. Once the leak was removed, the same model scored 0.26. This is the strongest practical validation of the External Independence Principle (Section 2.2): if the agent can see the answer, the evaluation is meaningless.
- **Phase 3: Human-approved remediation.** Add remediation recommendations to agent output. Add action safety scoring. Track human operator agreement rates over time.
- **Phase 4: Policy-bounded autonomy.** Define policy gates for low-risk automated actions. Expand the scenario library. The harness provides continuous assurance rather than periodic evaluation.

Trust is earned through accumulated evidence of correct behavior across progressively challenging conditions.

## 14.2 Scenario Library Development

A robust evaluation program requires scenarios covering the failure modes relevant to your workloads:

- **Resource exhaustion:** CPU saturation, memory pressure, disk I/O saturation
- **Application failures:** CrashLoopBackOff from bad configuration, OOMKilled containers, image pull errors
- **Network disruption:** Latency injection, packet loss, DNS resolution failures
- **Dependency failures:** Database connection pool exhaustion, upstream service unavailability
- **Configuration drift:** Misconfigured environment variables, incorrect resource limits, invalid secrets

Each scenario should have clear, unambiguous ground truth and produce observable telemetry. Scenarios should be versioned and maintained alongside the harness infrastructure.

---

# 15. Future Directions

## 15.1 Industry AIOps Benchmarks

The harness contract's open specification creates the foundation for **industry-standard AIOps benchmarks** analogous to MLPerf or the TPC benchmarks. The RCAEval benchmark [1] demonstrates the feasibility: 735 failure cases across 9 datasets revealed significant performance variation across methods. An industry AIOps benchmark would extend this to evaluating complete systems across all six scoring dimensions, including external eval model assessment.

## 15.2 Multi-Agent Operational Reasoning

Single-agent architectures may give way to **multi-agent systems** where specialized agents collaborate on complex incidents. The harness contract extends naturally to multi-agent evaluation by treating the ensemble as a single system under test while capturing inter-agent communication for auditability.

## 15.3 Federated Cross-Enterprise Learning

The standardized artifact format enables **federated evaluation** across organizations. Teams with similar workloads can share anonymized harness results to build collective understanding of AIOps capability without sharing proprietary telemetry data.

## 15.4 Continuous Regulatory Alignment

As AI governance frameworks mature, run bundles serve as the evidence artifacts that regulators require. Future work includes producing compliance-specific reporting formats (SOC 2 evidence packages, FedRAMP continuous monitoring reports) directly from run bundle data.

All of these directions are grounded in the same principle: **evidence-based governance** of AI-driven operational decisions.

---

# 16. Conclusion

AIOps can't succeed through models alone. Capability without governance produces systems that are powerful but untrustworthy, and untrustworthy systems don't reach production.

The harness-first architecture addresses this with four pillars:

- **Repeatable evaluation** through deterministic fault injection and versioned manifests. Same scenarios, comparable results across model updates.
- **External governance** through separation of the AI system from its evaluation framework. Policy gates define remediation boundaries. The two-tier scoring system (deterministic gate + external eval model assessment) establishes measurable standards that catch false positives a single-tier system would miss.
- **Evidence-grounded reasoning** through tool-mediated retrieval that connects every conclusion to retrievable telemetry. The external eval model verifies that conclusions are actually supported by tool results, not hallucinated. Full tool-call logs create an audit trail.
- **Continuous improvement** through structured feedback loops. The maturity model provides a governed progression from observation to bounded autonomy, each stage justified by accumulated scores.

These pillars aren't theoretical. Across seven benchmark rounds evaluating five model configurations, the architecture demonstrated that a 1B-parameter model augmented with curated documentation can approach the diagnostic accuracy of an 80B model, that deterministic keyword-matching scores produce false confidence when used alone, and that external eval model assessment reliably identifies hallucinated evidence that would otherwise go undetected.

The architecture's evaluation model — fact-checking pipeline claims using only the data the harness provides — catches errors that output-only evaluation misses. MLFlow serves as the central experiment tracking backbone, providing searchable, comparable records of every pipeline investigation and every harness evaluation. The dual-instance architecture (AIOps MLFlow for pipeline behavior, Harness MLFlow for evaluation results) maintains the External Independence Principle while enabling regression testing across model updates, prompt changes, and infrastructure changes. Alert-triggered detection with MTTD measurement bridges the gap between controlled benchmarking and production-ready incident response.

This isn't an easy problem, and this type of approach takes constant evaluation and refinement as your models and infrastructure evolve. But in the end, the path to trustworthy AI operations starts with the same principle that guides any good engineering decision: you need data, not guesswork.

> **Trust in AI Operations begins with measurable truth.**

---

# References

*Last verified: February 2026. Market figures, vendor positioning, and regulatory counts are time-sensitive and should be re-verified before citation.*

**Academic and Benchmark Research**

1. Pham, L., Zhang, H., Ha, H., Salim, F., & Zhang, X. (2024). *RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data.* arXiv. https://arxiv.org/abs/2412.17015

2. Goel, D., Magazine, R., Ghosh, S., Nambi, A., Deshpande, P., Zhang, X., et al. (2025). *eARCO: Efficient Automated Root Cause Analysis with Prompt Optimization.* arXiv. https://arxiv.org/abs/2504.11505

3. Zhang, X., Ghosh, S., Bansal, C., Wang, R., Ma, M., Kang, Y., Rajmohan, S. (2024). *Automated Root Causing of Cloud Incidents using In-Context Learning with GPT-4.* arXiv. https://arxiv.org/abs/2401.13810

4. Szandala, T. (2025). *AIOps for Reliability: Evaluating Large Language Models.* International Conference on Computational and Communication Systems (ICCS).

5. Yao, S., Zhao, J., Yu, D., et al. (2022). *ReAct: Synergizing Reasoning and Acting in Language Models.* arXiv. https://arxiv.org/abs/2210.03629

6. Schick, T., Dwivedi-Yu, J., Dessi, R., et al. (2023). *Toolformer: Language Models Can Teach Themselves to Use Tools.* arXiv. https://arxiv.org/abs/2302.04761

7. Zheng, L., Chiang, W., Sheng, Y., et al. (2023). *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.* NeurIPS 2023 Datasets and Benchmarks Track. https://arxiv.org/abs/2306.05685

8. Wang, H., Wu, Z., Jiang, H., et al. (2021). *Groot: An Event-graph-based Approach for Root Cause Analysis in Cloud Service Systems.* IEEE/ACM International Conference on Automated Software Engineering (ASE).

9. Chen, J., et al. (2023). *RCACopilot: Automated Root Cause Analysis with LLMs.* arXiv. https://arxiv.org/abs/2305.15778

**Enterprise AIOps Platforms**

10. ServiceNow. (2024). *What is ServiceNow AIOps?* https://www.servicenow.com/products/aiops.html

11. ServiceNow. (2026). *Now Assist for IT Operations Management (ITOM).* https://docs.servicenow.com/bundle/now-assist-itom

12. BigPanda. (2025). *Agentic AI for IT Operations.* https://www.bigpanda.io

13. Deepchecks. (2025). *Top 10 AIOps Tools for 2025.* Industry overview.

14. G2. (2025). *Best AIOps Tools and Platforms Reviews.* Market analysis.

**Market Reports**

15. MarketsandMarkets. (2023). *AIOps Platform Market Report (2023-2028).* Market research.

16. Splunk. (2025). *State of Observability 2025.* Industry survey report.

17. Gartner Peer Insights. (2025). *Best AIOps Platforms Reviews.*

18. Forrester Research. (2025). *The Forrester Wave: AIOps Platforms, Q2 2025.*

19. Omdia Tech. (2026). *Omdia Universe: AIOps, 2025-26.*

20. Gartner. (2025). *Magic Quadrant for Observability Platforms.*

**Standards and Governance**

21. ISO/IEC. (2023). *ISO/IEC 42001:2023, Artificial Intelligence Management System.*

22. NIST. (2023). *AI Risk Management Framework (AI RMF 1.0).*

23. European Parliament. (2024). *Regulation on Artificial Intelligence (EU AI Act).*

24. FedRAMP. (2026). *FedRAMP Authorization and AI Prioritization.* https://www.fedramp.gov

**Technology Platforms and Standards**

25. OpenTelemetry. (Ongoing). *OpenTelemetry Specification.* https://opentelemetry.io/docs/specs/

26. Prometheus. (Ongoing). *Prometheus Monitoring System.* https://prometheus.io/docs/

27. LitmusChaos. (Ongoing). *LitmusChaos: Cloud-Native Chaos Engineering.* https://litmuschaos.io

28. Chaos Mesh. (Ongoing). *Chaos Mesh: A Chaos Engineering Platform for Kubernetes.* https://chaos-mesh.org

29. Meta. (2024-2025). *Llama Stack: Unified AI Application Framework.* https://github.com/meta-llama/llama-stack

30. Nuland, C. (2021). *Metrics-Driven Pod Constraints.* Red Hat Blog. https://www.redhat.com/en/blog/metrics-driven-pod-constraints
