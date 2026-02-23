#!/usr/bin/env python3
"""Build a RAG knowledge base from actual OpenShift Lightspeed documentation.

Reads the pre-converted plaintext docs from the lightspeed-rag-content repo
and extracts the most relevant documents for AIOps incident investigation.

Usage:
    # First clone the lightspeed-rag-content repo:
    git clone --depth 1 https://github.com/openshift/lightspeed-rag-content.git /tmp/lightspeed-rag-content

    # Then run this script:
    python3 scripts/build_rag_from_lightspeed.py
"""

import json
import re
from pathlib import Path

# Source: clone of openshift/lightspeed-rag-content
RAG_CONTENT_DIR = Path("/tmp/lightspeed-rag-content/ocp-product-docs-plaintext/4.21")

# Output: knowledge base for our benchmark
OUTPUT_PATH = Path(__file__).parent / "rag_knowledge_base.json"

# Topics relevant to AIOps incident investigation
# Each entry: (file path relative to 4.21/, doc ID prefix, source label)
RELEVANT_DOCS = [
    # Monitoring & Observability
    ("observability/monitoring/about-ocp-monitoring.txt", "ocp-monitoring-overview",
     "Red Hat OpenShift Monitoring Documentation"),

    # Troubleshooting
    ("support/troubleshooting/investigating-pod-issues.txt", "ocp-troubleshoot-pods",
     "Red Hat OpenShift Support / Troubleshooting"),

    # Node & container resources
    ("nodes/nodes/nodes-nodes-resources-configuring.txt", "ocp-node-resources",
     "Red Hat OpenShift Node Management"),
    ("nodes/nodes/nodes-nodes-resources-cpus.txt", "ocp-node-cpu",
     "Red Hat OpenShift Node Management"),
    ("nodes/clusters/nodes-containers-events.txt", "ocp-container-events",
     "Red Hat OpenShift Container Events"),
    ("nodes/clusters/nodes-cluster-resource-levels.txt", "ocp-resource-levels",
     "Red Hat OpenShift Cluster Resource Management"),
    ("nodes/clusters/nodes-cluster-resource-configure.txt", "ocp-resource-configure",
     "Red Hat OpenShift Cluster Resource Management"),

    # Pod autoscaling & management
    ("nodes/pods/nodes-pods-autoscaling.txt", "ocp-pod-autoscaling",
     "Red Hat OpenShift Pod Autoscaling"),

    # Service Mesh (Istio)
    ("service_mesh/v2x/ossm-troubleshooting-istio.txt", "ocp-istio-troubleshoot",
     "Red Hat OpenShift Service Mesh Troubleshooting"),
    ("service_mesh/v2x/ossm-traffic-manage.txt", "ocp-istio-traffic",
     "Red Hat OpenShift Service Mesh Traffic Management"),

    # OTel
    ("observability/otel/otel-troubleshooting.txt", "ocp-otel-troubleshoot",
     "Red Hat OpenShift Distributed Tracing / OpenTelemetry"),
]

# Maximum chunk size in characters (keeps context manageable for small models)
MAX_CHUNK_SIZE = 2000


def chunk_document(text: str, max_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """Split a document into chunks at paragraph boundaries."""
    # Split on double newlines (paragraph breaks)
    paragraphs = re.split(r'\n{2,}', text)
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 > max_size and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def extract_title(text: str) -> str:
    """Extract the title from the first heading line."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("# ").strip()
    return "Untitled"


def build_knowledge_base():
    if not RAG_CONTENT_DIR.exists():
        print(f"ERROR: {RAG_CONTENT_DIR} not found.")
        print("Clone the repo first:")
        print("  git clone --depth 1 https://github.com/openshift/lightspeed-rag-content.git /tmp/lightspeed-rag-content")
        return

    documents = []

    for rel_path, id_prefix, source in RELEVANT_DOCS:
        doc_path = RAG_CONTENT_DIR / rel_path
        if not doc_path.exists():
            print(f"  SKIP (not found): {rel_path}")
            continue

        text = doc_path.read_text(encoding="utf-8")
        title = extract_title(text)

        # Chunk the document
        chunks = chunk_document(text)
        print(f"  {rel_path}: {len(chunks)} chunk(s) — \"{title}\"")

        for i, chunk in enumerate(chunks):
            doc_id = f"{id_prefix}-{i}" if len(chunks) > 1 else id_prefix
            documents.append({
                "id": doc_id,
                "source": source,
                "title": title if i == 0 else f"{title} (continued {i+1}/{len(chunks)})",
                "content": chunk,
                "ocp_version": "4.21",
                "lightspeed_source": str(rel_path),
            })

    # --- Supplementary BYOK content ---
    # In a real Lightspeed deployment these would be added via the BYOK
    # (Bring Your Own Knowledge) pipeline.  They cover PromQL metric
    # references and application-specific architecture that the standard
    # OCP docs do not include.
    byok = _byok_supplements()
    documents.extend(byok)
    print(f"\n  + {len(byok)} BYOK supplement(s)")

    # Write output
    with open(OUTPUT_PATH, "w") as f:
        json.dump(documents, f, indent=2)

    print(f"\nWrote {len(documents)} document chunks to {OUTPUT_PATH}")
    print(f"Total content size: {sum(len(d['content']) for d in documents):,} chars")


# ---------------------------------------------------------------------------
# BYOK supplements — PromQL references and app architecture
# ---------------------------------------------------------------------------

def _byok_supplements() -> list[dict]:
    """Curated BYOK content that supplements the standard OCP docs.

    These mirror what an SRE team would add to Lightspeed via the BYOK
    pipeline: PromQL cheat sheets, metric name references, and
    application-specific architecture documentation.
    """
    return [
        {
            "id": "byok-promql-cpu",
            "source": "BYOK / PromQL Metric Reference",
            "title": "Container CPU Metrics and PromQL Patterns",
            "content": (
                "Container CPU usage in OpenShift is tracked by the counter metric "
                "'container_cpu_usage_seconds_total'. To measure current CPU "
                "utilization rate, use:\n\n"
                "  rate(container_cpu_usage_seconds_total{namespace=\"<ns>\", "
                "pod=~\"<pod-pattern>.*\", container!=\"\"}[5m])\n\n"
                "The 'container!=\"\"' filter excludes the pause container. Values "
                "represent CPU cores consumed (e.g. 0.5 = half a core). CPU "
                "saturation is indicated when usage approaches or exceeds the "
                "container's CPU limit.\n\n"
                "Related metrics:\n"
                "- kube_pod_container_resource_limits{resource=\"cpu\"} — configured "
                "CPU limits per container\n"
                "- container_cpu_cfs_throttled_periods_total / "
                "container_cpu_cfs_periods_total — throttling ratio\n"
                "- kube_pod_container_resource_requests{resource=\"cpu\"} — CPU requests\n\n"
                "Important: For Prometheus range queries (query_range API), the "
                "PromQL expression must return an instant vector, not a range "
                "vector. Use rate(metric[5m]) not metric[5m]."
            ),
            "ocp_version": "4.21",
            "lightspeed_source": "byok/promql-cpu-reference.md",
        },
        {
            "id": "byok-promql-memory",
            "source": "BYOK / PromQL Metric Reference",
            "title": "Container Memory Metrics and OOMKill Detection",
            "content": (
                "Container memory usage is tracked by "
                "'container_memory_working_set_bytes', which represents the current "
                "working set memory. This is the metric Kubernetes uses for OOMKill "
                "decisions.\n\n"
                "  container_memory_working_set_bytes{namespace=\"<ns>\", "
                "pod=~\"<pod>.*\", container!=\"\"}\n\n"
                "Related metrics:\n"
                "- kube_pod_container_resource_limits{resource=\"memory\"} — memory limits\n"
                "- container_memory_rss — resident set size\n"
                "- kube_pod_container_status_terminated_reason{reason=\"OOMKilled\"}\n"
                "- kube_pod_container_status_restarts_total — cumulative restart count"
            ),
            "ocp_version": "4.21",
            "lightspeed_source": "byok/promql-memory-reference.md",
        },
        {
            "id": "byok-promql-patterns",
            "source": "BYOK / PromQL Metric Reference",
            "title": "Common PromQL Patterns for Incident Investigation",
            "content": (
                "Common PromQL patterns for operational analysis:\n\n"
                "1) Rate of counter:\n"
                "   rate(metric_total{labels}[5m])\n"
                "   Always use rate() with counter metrics, never query raw counters.\n\n"
                "2) Error rate:\n"
                "   rate(http_requests_total{code=~\"5..\"}[5m]) / "
                "rate(http_requests_total[5m])\n\n"
                "3) Aggregation across pods:\n"
                "   sum(rate(container_cpu_usage_seconds_total{namespace=\"ns\"}[5m])) "
                "by (pod)\n\n"
                "4) Top consumers:\n"
                "   topk(5, rate(container_cpu_usage_seconds_total[5m]))\n\n"
                "5) Pod restarts in the last hour:\n"
                "   increase(kube_pod_container_status_restarts_total{namespace=\"ns\"}"
                "[1h]) > 0\n\n"
                "Common mistakes:\n"
                "- Using [5m] range selector without wrapping in rate/increase\n"
                "- Mixing up range vectors and instant vectors in query_range calls\n"
                "- Forgetting container!=\"\" filter (includes pause containers)\n"
                "- Using 'pod_name' instead of 'pod' (label was renamed in newer K8s)"
            ),
            "ocp_version": "4.21",
            "lightspeed_source": "byok/promql-patterns.md",
        },
        {
            "id": "byok-cpu-saturation-runbook",
            "source": "BYOK / SRE Runbook",
            "title": "Runbook: Diagnosing CPU Saturation in OpenShift Pods",
            "content": (
                "CPU saturation occurs when a container's CPU usage approaches its "
                "resource limit, causing CPU throttling.\n\n"
                "Symptoms: increased request latency, timeout errors, pod restarts.\n\n"
                "Diagnosis steps:\n"
                "1) Query current CPU usage per container:\n"
                "   rate(container_cpu_usage_seconds_total{namespace=\"<ns>\", "
                "pod=~\"<pod>.*\", container!=\"\"}[5m])\n\n"
                "2) Compare against configured limits:\n"
                "   kube_pod_container_resource_limits{resource=\"cpu\", "
                "namespace=\"<ns>\"}\n\n"
                "3) Check throttling ratio:\n"
                "   rate(container_cpu_cfs_throttled_periods_total{namespace=\"<ns>\"}"
                "[5m]) / rate(container_cpu_cfs_periods_total{namespace=\"<ns>\"}[5m])\n\n"
                "4) Review Kubernetes events for 'Killing' or restart reasons.\n\n"
                "5) Check for sidecar containers or init containers consuming "
                "unexpected resources. A misbehaving sidecar can consume resources "
                "intended for the primary application.\n\n"
                "Common causes: traffic spike, resource-intensive sidecar, "
                "misconfigured resource limits, runaway process.\n\n"
                "Remediation: increase CPU limits, remove offending sidecar, "
                "configure HPA, or optimize application code."
            ),
            "ocp_version": "4.21",
            "lightspeed_source": "byok/runbook-cpu-saturation.md",
        },
        {
            "id": "byok-k8s-events",
            "source": "BYOK / SRE Runbook",
            "title": "Interpreting Kubernetes Events for Incident Analysis",
            "content": (
                "Kubernetes events provide a timeline of cluster state changes. Key "
                "event reasons for incident analysis:\n\n"
                "- Killing — container being stopped (liveness probe failure or "
                "resource pressure)\n"
                "- Created / Started — new container lifecycle beginning\n"
                "- Pulled / Pulling — image operations\n"
                "- FailedScheduling — insufficient resources for pod scheduling\n"
                "- Unhealthy — failed readiness/liveness probe\n"
                "- BackOff — container restart backoff (CrashLoopBackOff)\n"
                "- SuccessfulDelete / SuccessfulCreate — ReplicaSet operations\n\n"
                "When analyzing events, correlate timestamps with metric anomalies "
                "to establish causation. Look for the sequence: Killing -> "
                "SuccessfulDelete -> Created -> Started -> Pulled, which indicates "
                "a pod restart cycle."
            ),
            "ocp_version": "4.21",
            "lightspeed_source": "byok/runbook-k8s-events.md",
        },
        {
            "id": "byok-bookinfo-arch",
            "source": "BYOK / Application Architecture",
            "title": "Bookinfo Application Architecture and Dependencies",
            "content": (
                "Bookinfo is a polyglot microservices application with four "
                "services:\n\n"
                "1) productpage (Python) — the frontend, calls details and reviews\n"
                "2) details (Ruby) — provides book details, independent service\n"
                "3) reviews (Java) — provides book reviews, has three versions:\n"
                "   - v1: no ratings\n"
                "   - v2: black star ratings, calls ratings service\n"
                "   - v3: red star ratings, calls ratings service\n"
                "4) ratings (Node.js) — provides star ratings\n\n"
                "Dependency chain: productpage -> reviews -> ratings\n\n"
                "If the reviews service degrades, productpage will experience "
                "increased latency or errors since it synchronously calls reviews. "
                "If ratings degrades, only reviews v2 and v3 are affected."
            ),
            "ocp_version": "4.21",
            "lightspeed_source": "byok/bookinfo-architecture.md",
        },
    ]


if __name__ == "__main__":
    print(f"Building RAG knowledge base from Lightspeed docs ({RAG_CONTENT_DIR})...\n")
    build_knowledge_base()
