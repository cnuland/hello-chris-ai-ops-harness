Research Overview

We performed a broad, multidisciplinary survey of materials in the past ~2 years across several domains:

1) Academic Research — AIOps, Evaluation, and Benchmarks
AIOps Evaluation Challenges

Research studies highlight that RCA and anomaly detection in distributed systems lack standard evaluation frameworks and reproducible datasets. Benchmarks like RCAEval were created precisely because traditional systems did not include reliable metrics or environments to test root cause algorithms.

Scholarly surveys on AIOps emphasize the need for formal evaluation methodologies that:

separate training, evaluation, and test sets

provide baseline comparisons

measure multi-signal causal reasoning

quantify false positives and missed detections

These papers helped ground our assertion that “AIOps is fundamentally an evaluation problem” and informed the design of our Harness Contract.

2) Observability Standards — OpenTelemetry and Prometheus
OpenTelemetry

OpenTelemetry has emerged as the standard for telemetry signals (metrics, logs, traces, resources, events), and projects have stabilized signal definitions.

The emphasis on OpenTelemetry as a vendor-neutral evidence layer was validated through multiple research blog posts and official project documents.

Prometheus

Prometheus remains the predominant time-series platform in Kubernetes environments. Its query language (PromQL) and integration with SLO tooling make it ideally suited for reliable incident evidence capture.

These sources supported our architectural choice of Prometheus + OpenTelemetry as the evidence foundation in the white paper.

3) Enterprise AIOps Product Research

We surveyed the state of enterprise AIOps platforms and notes on how they evaluate AI behavior:

ServiceNow Now Assist for ITOM

Provides generative summaries and investigative context for alerts, but lacks standard scorecards or independent evaluation.

Datadog Watchdog RCA

Automates root cause detection using ML, but the evaluation is tied to Datadog’s internal models rather than a portable harness.

Dynatrace AI/Preventive Ops

Offers predictive operational insights, but again without a standardized, repeatable, vendor-agnostic evaluation contract.

BigPanda Biggy

Represents the first wave of applying generative AI for incident analysis, but the ecosystem lacks standardized scoring and audit trails.

These products informed our competitor analysis and reinforced the need for an external, portable harness.

4) Open Source AIOps Ecosystem & Community Projects

We reviewed the lfedgeai AIOps project which frames AIOps evaluation as a community problem:

The project advocates for AIOps test harnesses

Calls for separating data into TRAIN/EVAL/TEST so tuning doesn’t skew final reports

Recommends modular “Bring Your Own Brain” evaluation with retrieval tools

Prioritizes chaos engineering style baseline/inject/collect workflows

These ideas directly informed the structure of our white paper’s harness definitions, contract patterns, and pipeline.

5) Multi-Signal Reasoning & LLM + Tool Use Research

Research on LLMs interacting with external tools has proliferated in the last 2 years:

Tool usage guidelines for LLM reasoning emphasize:

bounded tool access

schema-validated evidence retrieval

explainable reasoning chains

Studies show that LLMs require structured evidence (not raw logs) for reliable RCA

These bodies of research shaped our stance on tool-mediated retrieval architectures (e.g., Llama Stack + tools instead of raw telemetry dump).

Project Objective (Concise Statement)

To define, formalize, and validate a repeatable, portable, and auditable AIOps evaluation methodology — the “Harness-First AIOps Architecture” — that enables organizations to measure, score, compare, and improve AI-driven operational intelligence in distributed infrastructure environments, with explicit evidence contracts, scoring rubrics, and governance boundaries.

Our objective is not just a theoretical framework — it has practical engineering outcomes:

A harness specification (contract) enabling reliable evaluation of RCA and remediation reasoning.

A reference architecture that ties OpenShift observability (OpenTelemetry, Prometheus, events) into AIOps reasoning systems (Llama Stack).

A scoring and judge model framework that quantifies AIOps performance across key dimensions.

Extensible patterns for continuous refinement using OpenShift AI pipelines — without compromising production safety.

Extensive examples (Bookinfo CPU saturation, CrashLoopBackOff) with canonical artifacts (run.json, truth.json, score.json, etc.).

Vision for broader ecosystem alignment (open benchmarks, multi-agent orchestration, federated evaluation).

Citations

Below are the key research, products, and standards that underpinned the white paper — organized so you can reference them in your diagrams, methodology, and citations section.

📘 Scholarly Research & Benchmarks

RCAEval benchmark for root cause analysis evaluation in microservices

Surveys on AIOps in distributed systems: evaluation frameworks, gaps in reproducible measurement

Research on multi-signal reasoning for anomaly detection and causal inference

(These were synthesized from academic conference proceedings and arXiv preprints over the last 2 years.)

📊 Observability Standards & Integrations

OpenTelemetry project specification and telemetry standards

Prometheus documentation and integration patterns within Kubernetes

🛠 Enterprise AIOps Tools & Platforms

ServiceNow Now Assist for ITOM

Datadog Watchdog RCA

Dynatrace preventive operations

BigPanda Biggy AI for ITOps

🔄 Open Source AIOps Ecosystem

lfedgeai AIOps project (harness proposals, benchmark discussions, BYOB concepts)

Community discussions advocating chaos engineering patterns, dataset separation, tool access schemas

🧠 LLM + Tool Use Research

Tool-mediated retrieval for LLMs

Evidence-grounded reasoning patterns

Critiques of raw telemetry -> model ingestion

Summary of Research Contributions to the White Paper
White Paper Component	Primary Research & Sources
Harness Contract	RCAEval, lfedgeai docs, chaos experiment literature
Evidence Plane	OpenTelemetry + Prometheus standards
Scoring & Evaluation	Academic evaluation frameworks
AI Reasoning Architecture	LLM tool use research
Product Landscape	Enterprise AIOps product documentation
Continuous Improvement Loop	Reinforcement learning survey research


📘 Academic & Benchmark Research (AIOps, RCA, LLMs)

Pham, L., Zhang, H., Ha, H., Salim, F., & Zhang, X. (2024). RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data. arXiv. This work introduces RCAEval, an open benchmark providing datasets and an evaluation framework for RCA in microservices. It includes many failure cases and baselines for reproducible evaluation.

Goel, D., Magazine, R., Ghosh, S., Nambi, A., Deshpande, P., Zhang, X., et al. (2025). eARCO: Efficient Automated Root Cause Analysis with Prompt Optimization. arXiv. Research on optimizing prompts and LLM performance for RCA recommendation, demonstrating accuracy improvements via prompt optimization techniques.

Zhang, X., Ghosh, S., Bansal, C., Wang, R., Ma, M., Kang, Y., Rajmohan, S. (2024). Automated Root Causing of Cloud Incidents using In-Context Learning with GPT-4. arXiv. A study on using in-context learning with LLMs for RCA without expensive fine-tuning, showing strong performance improvements on incident datasets.

Szandała, T. (2025). AIOps for Reliability: Evaluating Large Language Models. International Conference on Computational and Communication Systems (ICCS). Evaluation of LLMs for incident RCA using chaos experiments, exploring LLM capability in controlled failure diagnosis.

🛠 Industry Reports & Enterprise AIOps Platforms

ServiceNow. (2024). What is ServiceNow AIOps? ServiceNow product documentation. Overview of AIOps capabilities within ServiceNow IT Operations Management (ITOM), including predictive analytics and ML-driven automation.

ServiceNow. (2026). Now Assist for IT Operations Management (ITOM). ServiceNow release notes. Description of agentic workflows that integrate observability tools for alert impact analysis.

BigPanda. (2025). Agentic AI for IT Operations. BigPanda corporate site. Details BigPanda’s AI-driven event correlation and incident intelligence platform for enterprise operations.

Deepchecks. (2025). Top 10 AIOps Tools for 2025. Deepchecks industry overview of AIOps platforms, including Dynatrace and others with automated RCA features.

G2. (2025). Best AIOps Tools and Platforms Reviews. G2 user-review-based comparison of AIOps platforms, highlighting ServiceNow IT Operations Management and BigPanda.

Other vendor comparisons and lists (e.g., USAII Top 12 AIOps Tools, CloudEagle Top 10, Freshworks 15 Best AIOps Tools) illustrate broader ecosystem capabilities and market trends in automatic correlation, anomaly detection, and incident resolution.

📊 Observability & Telemetry Standards

OpenTelemetry. (Ongoing). OpenTelemetry Specification. OpenTelemetry community defining vendor-neutral telemetry standards for metrics, logs, and traces. (Common industry reference for evidence layer design.)

Prometheus. (Ongoing). Prometheus Monitoring System. Widely used time-series database and query engine in Kubernetes observability, supporting quantitative evidence capture for incidents.

(Note: Prometheus and OpenTelemetry are standard technologies referenced in observability research and product documentation; specific academic citations are not provided here but should be cited according to project documentation when included in a published white paper.)

🧠 LLM Reasoning & Tool Integration Patterns

Research in recent years has shown that LLM + tool-mediated retrieval models improve deterministic reasoning and explainability compared to ingesting raw telemetry streams. While not tied to a single paper, this concept is reflected across tool-agent literature in 2024–25 in AI systems research.

📈 AIOps Market Insights & Analyst Inputs

Gartner Peer Insights. (2025). Best AIOps Platforms Reviews. Describes the core capabilities of AIOps platforms—cross-domain ingestion, topology assembly, correlation, pattern recognition—offering broader context on industry expectations.

Omdia Tech. (2026). Omdia Universe: AIOps, 2025–26. (Analyst survey redefining enterprise AIOps capabilities across vendors.)

📚 Suggested Additional Readings (Optional Context)

Exploratory analysis of RCA approaches and industrial practices (e.g., event-graph and causal relationships in distributed systems), such as event-graph based RCA (e.g., Groot study, 2021).

How to Cite These in Your White Paper (Markdown)

Use the following list in your References section:

**Academic & Benchmark Research**
1. Pham, L., Zhang, H., Ha, H., Salim, F., & Zhang, X. (2024). *RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data.* arXiv. https://arxiv.org/abs/2412.17015 :contentReference[oaicite:13]{index=13}
2. Goel, D., Magazine, R., Ghosh, S., Nambi, A., Deshpande, P., Zhang, X., et al. (2025). *eARCO: Efficient Automated Root Cause Analysis with Prompt Optimization.* arXiv. https://arxiv.org/abs/2504.11505 :contentReference[oaicite:14]{index=14}
3. Zhang, X., Ghosh, S., Bansal, C., Wang, R., Ma, M., Kang, Y., Rajmohan, S. (2024). *Automated Root Causing of Cloud Incidents using In-Context Learning with GPT-4.* arXiv. https://arxiv.org/abs/2401.13810 :contentReference[oaicite:15]{index=15}
4. Szandała, T. (2025). *AIOps for Reliability: Evaluating Large Language Models.* ICCS 2025. :contentReference[oaicite:16]{index=16}

**Enterprise AIOps Platforms & Market Reports**
5. ServiceNow. (2024). *What is ServiceNow AIOps?* https://www.servicenow.com/products/it-operations-management/what-is-aiops.html :contentReference[oaicite:17]{index=17}
6. ServiceNow. (2026). *Now Assist for ITOM.* https://www.servicenow.com/docs/bundle/yokohama-release-notes/... :contentReference[oaicite:18]{index=18}
7. BigPanda. (2025). *Agentic AI for IT Operations.* https://www.bigpanda.io/ :contentReference[oaicite:19]{index=19}
8. Deepchecks. (2025). *Top 10 AIOps Tools for 2025.* https://www.deepchecks.com/top-10-aiops-tools-2025/ :contentReference[oaicite:20]{index=20}
9. G2. (2025). *Best AIOps Tools and Platforms Reviews.* https://www.g2.com/categories/aiops-platforms :contentReference[oaicite:21]{index=21}
10. Various industry lists on AIOps capabilities (2025–2026). :contentReference[oaicite:22]{index=22}

**Analyst Market Reports**
11. Gartner Peer Insights. (2025). *Best AIOps Platforms Reviews.* https://www.gartner.com/reviews/market/aiops-platforms :contentReference[oaicite:23]{index=23}
12. Omdia Tech. (2026). *Omdia Universe: AIOps, 2025–26.* :contentReference[oaicite:24]{index=24}

**Suggested Additional Context**
13. Wang, H., Wu, Z., Jiang, H., et al. (2021). *Groot: An Event-graph-based Approach for Root Cause Analysis.* arXiv. :contentReference[oaicite:25]{index=25}

