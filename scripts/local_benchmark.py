#!/usr/bin/env python3
"""Local harness benchmark — runs CPU saturation scenario against multiple LLM endpoints.

Runs locally (no container build required) using:
  - Local kubeconfig for K8s API (fault injection + events)
  - Thanos route for Prometheus queries (via OC token)
  - Direct HTTP calls to LLM endpoints (vLLM + Gemini)

Usage:
    python3 scripts/local_benchmark.py
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
from kubernetes import client, config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("local-benchmark")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

THANOS_ROUTE = os.environ.get("THANOS_ROUTE", "")
if not THANOS_ROUTE:
    # Auto-discover Thanos route from the cluster
    _thanos = subprocess.run(
        ["oc", "get", "route", "thanos-querier", "-n", "openshift-monitoring",
         "-o", "jsonpath={.spec.host}"],
        capture_output=True, text=True,
    )
    THANOS_ROUTE = f"https://{_thanos.stdout.strip()}" if _thanos.stdout.strip() else ""
    if not THANOS_ROUTE:
        print("ERROR: Could not discover Thanos route. Set THANOS_ROUTE env var.")
        sys.exit(1)

# Get OC token for Thanos auth
def _get_oc_token() -> str:
    result = subprocess.run(["oc", "whoami", "-t"], capture_output=True, text=True)
    return result.stdout.strip()

OC_TOKEN = _get_oc_token()

NAMESPACE = "bookinfo"
DEPLOYMENT = "reviews-v2"
FAULT_TYPE = "cpu_saturation"

BASELINE_WAIT = 30      # seconds (shortened for local run)
INJECTION_WAIT = 90     # seconds for fault to propagate

# ---------------------------------------------------------------------------
# RAG Knowledge Base (simulates OpenShift Lightspeed documentation retrieval)
# ---------------------------------------------------------------------------

_KB_PATH = Path(__file__).parent / "rag_knowledge_base.json"
_KNOWLEDGE_BASE: list[dict] = []


def _load_knowledge_base():
    global _KNOWLEDGE_BASE
    if _KNOWLEDGE_BASE:
        return
    if _KB_PATH.exists():
        with open(_KB_PATH) as f:
            _KNOWLEDGE_BASE = json.load(f)
        log.info(f"Loaded RAG knowledge base: {len(_KNOWLEDGE_BASE)} documents")
    else:
        log.warning(f"Knowledge base not found at {_KB_PATH}")


def search_documentation(query: str, top_k: int = 3) -> list[dict]:
    """Simple keyword-based document retrieval over the curated knowledge base.

    In a production setup this would use an embedding model + vector store
    (like OpenShift Lightspeed does with RHEL/OCP docs). For this demo we
    use TF-IDF-style keyword matching to keep dependencies minimal.
    """
    _load_knowledge_base()
    if not _KNOWLEDGE_BASE:
        return []

    query_terms = set(re.split(r'\W+', query.lower())) - {"", "the", "a", "an", "in", "of", "for", "to", "and", "or", "is", "it", "by"}

    scored = []
    for doc in _KNOWLEDGE_BASE:
        text = f"{doc.get('title', '')} {doc.get('content', '')}".lower()
        text_terms = set(re.split(r'\W+', text))
        # Count matching terms weighted by specificity
        matches = query_terms & text_terms
        score = len(matches)
        # Boost for title matches
        title_terms = set(re.split(r'\W+', doc.get('title', '').lower()))
        title_matches = query_terms & title_terms
        score += len(title_matches) * 2
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "title": doc["title"],
            "source": doc["source"],
            "content": doc["content"],
        }
        for _, doc in scored[:top_k]
    ]


# Model endpoints to benchmark
MODELS = {
    "granite-4-tiny": {
        "name": "Granite 4.0-H-Tiny (1B active, local MIG)",
        "base_url": None,  # resolved dynamically via oc port-forward
        "model_id": "granite-4",
        "headers": {},
        "max_tokens": 4096,
    },
    "granite-4-tiny-lightspeed": {
        "name": "Granite 4.0-H-Tiny + Lightspeed (1B active, local MIG)",
        "base_url": None,  # resolved dynamically — same endpoint as granite-4-tiny
        "model_id": "granite-4",
        "headers": {},
        "max_tokens": 4096,
        "rag_enabled": True,
    },
    "gemini-3-pro": {
        "name": "Gemini 3 Pro (SaaS, Google AI Studio)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3-pro-preview",
        "headers": {},  # API key set dynamically
        "max_tokens": 8192,
    },
    "qwen3-coder-next": {
        "name": "Qwen3-Coder-Next (80B MoE, 3B active, 2x H200)",
        "base_url": None,  # resolved dynamically via route
        "model_id": "qwen3-coder-next",
        "headers": {},
        "max_tokens": 8192,
    },
    "qwen3-coder-next-lightspeed": {
        "name": "Qwen3-Coder-Next + Lightspeed (80B MoE, 3B active, 2x H200)",
        "base_url": None,  # resolved dynamically — same endpoint as qwen3-coder-next
        "model_id": "qwen3-coder-next",
        "headers": {},
        "max_tokens": 8192,
        "rag_enabled": True,
    },
}


# ---------------------------------------------------------------------------
# Prometheus queries (local version using Thanos route + OC token)
# ---------------------------------------------------------------------------

async def query_prometheus(query: str, start: str = None, end: str = None,
                           raise_on_error: bool = False) -> dict:
    """Query Thanos via the external route using OC token."""
    headers = {"Authorization": f"Bearer {OC_TOKEN}"}
    async with httpx.AsyncClient(verify=False, timeout=30.0) as c:
        try:
            if start and end:
                resp = await c.get(
                    f"{THANOS_ROUTE}/api/v1/query_range",
                    params={"query": query, "start": start, "end": end, "step": "30s"},
                    headers=headers,
                )
            else:
                resp = await c.get(
                    f"{THANOS_ROUTE}/api/v1/query",
                    params={"query": query},
                    headers=headers,
                )
            if resp.status_code != 200:
                error_msg = f"Prometheus returned HTTP {resp.status_code}: {resp.text[:300]}"
                if raise_on_error:
                    raise httpx.HTTPStatusError(error_msg, request=resp.request, response=resp)
                return {"status": "error", "error": error_msg, "resultCount": 0, "data": []}
            return _summarize_prom(resp.json())
        except httpx.HTTPStatusError:
            raise
        except Exception as e:
            if raise_on_error:
                raise
            return {"status": "error", "error": str(e), "resultCount": 0, "data": []}


def _summarize_prom(prom_response: dict) -> dict:
    """Compact summary of Prometheus response."""
    result_type = prom_response.get("data", {}).get("resultType", "unknown")
    results = prom_response.get("data", {}).get("result", [])
    summarized = []
    for r in results[:20]:
        metric = r.get("metric", {})
        if result_type == "matrix":
            values = r.get("values", [])
            nums = [float(v[1]) for v in values if v[1] != "NaN"]
            summarized.append({
                "metric": metric,
                "samples": len(values),
                "min": round(min(nums), 4) if nums else None,
                "max": round(max(nums), 4) if nums else None,
                "avg": round(sum(nums) / len(nums), 4) if nums else None,
                "latest": values[-1][1] if values else None,
            })
        elif result_type == "vector":
            value = r.get("value", [None, None])
            summarized.append({"metric": metric, "value": value[1] if len(value) > 1 else None})
        else:
            summarized.append({"metric": metric, "raw": r})
    return {"status": prom_response.get("status"), "resultType": result_type,
            "resultCount": len(results), "data": summarized}


# ---------------------------------------------------------------------------
# K8s helpers
# ---------------------------------------------------------------------------

def load_k8s():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def get_k8s_events(namespace: str, since_minutes: int = 30) -> list:
    load_k8s()
    v1 = client.CoreV1Api()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    events_list = v1.list_namespaced_event(namespace=namespace)
    results = []
    for ev in events_list.items:
        event_time = ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp
        if event_time and event_time.replace(tzinfo=timezone.utc) < cutoff:
            continue
        results.append({
            "type": ev.type, "reason": ev.reason, "message": ev.message,
            "count": ev.count,
            "involved_object": {
                "kind": ev.involved_object.kind,
                "name": ev.involved_object.name,
            },
            "last_timestamp": event_time.isoformat() if event_time else None,
        })
    results.sort(key=lambda e: e.get("last_timestamp") or "", reverse=True)
    return results[:50]


def search_pod_logs(namespace: str, search_text: str = "error", limit: int = 50) -> list:
    load_k8s()
    v1 = client.CoreV1Api()
    pod_list = v1.list_namespaced_pod(namespace=namespace)
    results = []
    for p in pod_list.items:
        if p.status.phase != "Running":
            continue
        try:
            log_text = v1.read_namespaced_pod_log(
                name=p.metadata.name, namespace=namespace,
                since_seconds=1800, tail_lines=100,
            )
            lines = log_text.strip().split("\n") if log_text else []
            if search_text:
                lines = [l for l in lines if search_text.lower() in l.lower()]
            for line in lines[:10]:
                results.append({"pod": p.metadata.name, "log": line})
        except Exception:
            pass
    return results[:limit]


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------

def inject_cpu_saturation(namespace: str, deployment_name: str):
    """Add a CPU-stress sidecar to the deployment.

    Uses ubi-minimal from Red Hat's registry (no Docker Hub rate limits) with a
    POSIX shell busy-loop to saturate one CPU core.  Patches via ``oc patch
    --type=merge`` (JSON merge patch) so the full container list is replaced
    rather than strategically merged by name.
    """
    load_k8s()
    apps_v1 = client.AppsV1Api()
    deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    containers = deploy.spec.template.spec.containers
    existing = [c.name for c in containers]
    if "stress-injector" in existing:
        log.warning("stress-injector already present — removing first")
        remove_cpu_saturation(namespace, deployment_name)
        # Re-read the deployment after cleanup
        deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)
        containers = deploy.spec.template.spec.containers

    stress_container = {
        "name": "stress-injector",
        "image": "registry.access.redhat.com/ubi9/ubi-minimal:latest",
        "command": ["sh", "-c"],
        "args": ["while true; do :; done"],
        "resources": {
            "requests": {"cpu": "100m", "memory": "32Mi"},
            "limits": {"cpu": "500m", "memory": "64Mi"},
        },
    }

    all_containers = [_c2d(c) for c in containers] + [stress_container]
    patch_json = json.dumps({"spec": {"template": {"spec": {"containers": all_containers}}}})
    result = subprocess.run(
        ["oc", "patch", "deployment", deployment_name, "-n", namespace,
         "--type=merge", "-p", patch_json],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"oc patch failed: {result.stderr}")
    log.info(f"Injected CPU saturation into {namespace}/{deployment_name}")


def remove_cpu_saturation(namespace: str, deployment_name: str):
    """Remove the stress-injector sidecar using JSON merge patch.

    Kubernetes strategic merge patch merges container arrays by name and cannot
    delete entries by omission.  ``oc patch --type=merge`` sends a JSON merge
    patch that replaces the entire containers array, reliably removing the
    sidecar.
    """
    load_k8s()
    apps_v1 = client.AppsV1Api()
    deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    containers = deploy.spec.template.spec.containers
    filtered = [c for c in containers if c.name != "stress-injector"]
    if len(filtered) == len(containers):
        return
    patch_body = {"spec": {"template": {"spec": {"containers": [_c2d(c) for c in filtered]}}}}
    result = subprocess.run(
        ["oc", "patch", "deployment", deployment_name, "-n", namespace,
         "--type=merge", "-p", json.dumps(patch_body)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"oc patch failed: {result.stderr}")
    log.info(f"Removed CPU saturation from {namespace}/{deployment_name}")


def _c2d(c) -> dict:
    if isinstance(c, dict):
        return c
    d = {"name": c.name, "image": c.image}
    if c.command: d["command"] = c.command
    if c.args: d["args"] = c.args
    if c.ports:
        d["ports"] = [{"containerPort": p.container_port, "protocol": p.protocol or "TCP"} for p in c.ports]
    if c.env:
        d["env"] = [{"name": e.name, "value": e.value} for e in c.env if e.value]
    if c.resources:
        res = {}
        if c.resources.requests: res["requests"] = c.resources.requests
        if c.resources.limits: res["limits"] = c.resources.limits
        if res: d["resources"] = res
    if c.image_pull_policy: d["imagePullPolicy"] = c.image_pull_policy
    if c.volume_mounts:
        d["volumeMounts"] = [{"name": vm.name, "mountPath": vm.mount_path} for vm in c.volume_mounts]
    return d


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

async def collect_evidence(namespace: str, deployment_name: str,
                           start_time: str, end_time: str) -> dict:
    evidence = {
        "collection_time": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start_time, "end": end_time},
        "metrics": {}, "events": [], "logs": [],
    }

    evidence["metrics"]["cpu"] = await query_prometheus(
        f'rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m])',
        start_time, end_time,
    )
    evidence["metrics"]["memory"] = await query_prometheus(
        f'container_memory_working_set_bytes{{namespace="{namespace}", '
        f'pod=~"{deployment_name}.*"}}',
        start_time, end_time,
    )
    evidence["metrics"]["restarts"] = await query_prometheus(
        f'kube_pod_container_status_restarts_total{{namespace="{namespace}"}}',
    )
    evidence["metrics"]["pod_status"] = await query_prometheus(
        f'kube_pod_status_phase{{namespace="{namespace}"}}',
    )
    evidence["events"] = get_k8s_events(namespace, since_minutes=15)
    evidence["logs"] = search_pod_logs(namespace, "error", 30)

    return evidence


def build_evidence_summary(evidence: dict) -> str:
    lines = []
    for name, data in evidence.get("metrics", {}).items():
        if isinstance(data, dict) and data.get("data"):
            for item in data["data"][:5]:
                m = item.get("metric", {})
                pod = m.get("pod", m.get("__name__", ""))
                if "value" in item:
                    lines.append(f"  {name}: pod={pod} value={item['value']}")
                elif "avg" in item:
                    lines.append(f"  {name}: pod={pod} avg={item['avg']} max={item['max']}")
    events = evidence.get("events", [])
    if events:
        lines.append(f"\nKubernetes Events ({len(events)} recent):")
        for ev in events[:8]:
            lines.append(f"  [{ev.get('type')}] {ev.get('reason')}: {ev.get('message', '')[:120]}")
    return "\n".join(lines) if lines else "No evidence collected"


# ---------------------------------------------------------------------------
# Tool execution (local — calls Prometheus/K8s directly)
# ---------------------------------------------------------------------------

async def execute_tool_call(tool_name: str, args: dict) -> dict:
    try:
        if tool_name == "getMetricHistory":
            return await query_prometheus(
                args.get("query", "up"),
                args.get("start"), args.get("end"),
            )
        elif tool_name == "getK8sEvents":
            return {"events": get_k8s_events(
                args.get("namespace", "bookinfo"),
                args.get("since_minutes", 30),
            )}
        elif tool_name == "searchLogs":
            return {"results": search_pod_logs(
                args.get("namespace", "bookinfo"),
                args.get("search_text", "error"),
                args.get("limit", 50),
            )}
        elif tool_name == "searchDocumentation":
            docs = search_documentation(
                args.get("query", ""),
                args.get("top_k", 3),
            )
            return {"documents": docs, "count": len(docs)}
        else:
            return {"status": "not_configured", "message": f"Tool {tool_name} not available"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:500]}


# ---------------------------------------------------------------------------
# Agent invocation (supports OpenAI-compatible + Gemini)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "getMetricHistory",
            "description": "Query Prometheus for metric history. Use PromQL queries to examine CPU, memory, latency, error rates for services in the bookinfo namespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "PromQL query string"},
                    "start": {"type": "string", "description": "RFC-3339 start time"},
                    "end": {"type": "string", "description": "RFC-3339 end time"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getK8sEvents",
            "description": "Retrieve Kubernetes events filtered by namespace and optionally by resource type/name. Shows pod crashes, restarts, scheduling failures, and other signals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "default": "bookinfo"},
                    "resource_type": {"type": "string", "description": "Filter by kind (Pod, Deployment)"},
                    "resource_name": {"type": "string", "description": "Filter by resource name"},
                    "since_minutes": {"type": "integer", "default": 30},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "searchLogs",
            "description": "Search pod logs for error patterns or specific text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "default": "bookinfo"},
                    "pod_name": {"type": "string"},
                    "search_text": {"type": "string"},
                    "since_minutes": {"type": "integer", "default": 30},
                },
            },
        },
    },
]

# Extended tool set including documentation search (for RAG-enabled models)
RAG_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "searchDocumentation",
        "description": (
            "Search curated OpenShift and Kubernetes documentation for operational "
            "knowledge. Use this to look up correct PromQL metric names, query "
            "patterns, troubleshooting procedures, and architecture details before "
            "querying live systems. This helps you write correct queries and "
            "interpret results accurately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language search query, e.g. 'CPU saturation "
                        "diagnosis PromQL' or 'Bookinfo service dependencies'"
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


RAG_SYSTEM_PROMPT = (
    "You are an expert SRE AI agent investigating an operational incident "
    "in a Kubernetes-based microservices application called Bookinfo. "
    "Services: productpage (frontend), details, reviews (v1, v2, v3), ratings. "
    "Dependency chain: productpage -> reviews -> ratings. "
    "You MUST use the available tools to gather evidence before drawing conclusions. "
    "Do not guess — query metrics, check events, and search logs to build your case. "
    "\n\n"
    "You have access to a searchDocumentation tool containing curated OpenShift "
    "and Kubernetes documentation (powered by OpenShift Lightspeed). If you are "
    "unsure about the correct PromQL metric name or query syntax, make ONE quick "
    "documentation search first, then immediately move on to querying live systems. "
    "Do NOT spend more than one tool call on documentation — your primary job is "
    "to investigate the actual incident using getMetricHistory, getK8sEvents, and "
    "searchLogs. "
    "\n\n"
    "After investigation, provide your findings as JSON with keys: "
    "incident_summary, rca_ranked (list of strings like 'bookinfo/reviews-v2:cpu_saturation'), "
    "recommended_action, evidence_links (list of strings referencing specific "
    "metrics or events you discovered)."
)

DEFAULT_SYSTEM_PROMPT = (
    "You are an expert SRE AI agent investigating an operational incident "
    "in a Kubernetes-based microservices application called Bookinfo. "
    "Services: productpage (frontend), details, reviews (v1, v2, v3), ratings. "
    "Dependency chain: productpage -> reviews -> ratings. "
    "You MUST use the available tools to gather evidence before drawing conclusions. "
    "Do not guess — query metrics, check events, and search logs to build your case. "
    "After investigation, provide your findings as JSON with keys: "
    "incident_summary, rca_ranked (list of strings like 'bookinfo/reviews-v2:cpu_saturation'), "
    "recommended_action, evidence_links (list of strings referencing specific "
    "metrics or events you discovered)."
)


async def invoke_agent(model_key: str, model_cfg: dict, evidence: dict,
                       incident_desc: str) -> dict:
    """Invoke LLM with tool-calling for RCA investigation.

    NOTE: We intentionally do NOT provide pre-collected evidence in the prompt.
    The agent must discover all evidence through tool calls. This prevents
    models from simply parroting back handed answers and ensures the harness
    tests genuine investigative ability.
    """
    rag_enabled = model_cfg.get("rag_enabled", False)
    system_prompt = RAG_SYSTEM_PROMPT if rag_enabled else DEFAULT_SYSTEM_PROMPT
    tools = TOOL_DEFINITIONS + ([RAG_TOOL_DEFINITION] if rag_enabled else [])

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"INCIDENT ALERT:\n{incident_desc}\n\n"
                "Use the available tools to investigate this incident. "
                "Query Prometheus metrics, check Kubernetes events, and search pod logs "
                "to determine the root cause. Provide your root cause analysis as JSON."
            ),
        },
    ]

    tool_calls_log = []
    base_url = model_cfg["base_url"]
    headers = {**model_cfg["headers"], "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300.0, verify=False) as c:
        # --- First call: with tools ---
        log.info(f"[{model_key}] Sending initial request with tools...")
        try:
            resp = await c.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model_cfg["model_id"],
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "max_tokens": model_cfg["max_tokens"],
                },
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            log.error(f"[{model_key}] Initial call failed: {e}")
            return _fallback_output(str(e), tool_calls_log)

        choices = result.get("choices", [])
        if not choices:
            return _fallback_output("No choices in response", tool_calls_log)

        message = choices[0].get("message", {})

        # --- Process tool calls ---
        max_rounds = 3
        round_num = 0
        while message.get("tool_calls") and round_num < max_rounds:
            round_num += 1
            log.info(f"[{model_key}] Processing {len(message['tool_calls'])} tool call(s) (round {round_num})...")

            # Add assistant message with tool calls
            messages.append(message)

            for tc in message["tool_calls"]:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    raw_args = fn.get("arguments", "{}")
                    # Handle double-stringified JSON from some models
                    if isinstance(raw_args, str):
                        tool_args = json.loads(raw_args)
                        if isinstance(tool_args, str):
                            tool_args = json.loads(tool_args)
                    else:
                        tool_args = raw_args
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                log.info(f"[{model_key}]   Tool: {tool_name}({json.dumps(tool_args)[:200]})")

                tool_result = await execute_tool_call(tool_name, tool_args)
                tool_calls_log.append({
                    "tool": tool_name,
                    "arguments": tool_args,
                    "result_summary": json.dumps(tool_result, default=str)[:500],
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{round_num}"),
                    "content": json.dumps(tool_result, default=str)[:3000],
                })

            # --- Follow-up call ---
            try:
                resp2 = await c.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": model_cfg["model_id"],
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": "auto",
                        "max_tokens": model_cfg["max_tokens"],
                    },
                )
                resp2.raise_for_status()
                result2 = resp2.json()
                choices = result2.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                else:
                    break
            except Exception as e:
                log.warning(f"[{model_key}] Follow-up call failed: {e}")
                break

        # --- If content is empty after tool rounds, make a final call without tools ---
        content = message.get("content", "")
        if not content or len(content) < 10:
            log.info(f"[{model_key}] Empty response after tool rounds, making final text-only call...")
            messages.append({"role": "user", "content": (
                "Based on all the tool results above, please provide your final root cause analysis "
                "as a JSON object with keys: incident_summary, rca_ranked (list of strings like "
                "'bookinfo/reviews-v2:cpu_saturation'), recommended_action, evidence_links (list of strings)."
            )})
            try:
                resp_final = await c.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": model_cfg["model_id"],
                        "messages": messages,
                        "max_tokens": model_cfg["max_tokens"],
                    },
                )
                resp_final.raise_for_status()
                result_final = resp_final.json()
                choices = result_final.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
            except Exception as e:
                log.warning(f"[{model_key}] Final text call failed: {e}")

        log.info(f"[{model_key}] Final response length: {len(content)} chars")
        return _parse_response(content, tool_calls_log)


def _parse_response(content: str, tool_calls: list) -> dict:
    """Parse LLM response into structured aiops_output."""
    # Try JSON extraction
    try:
        # Find JSON block (possibly in markdown code fence)
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(1))
            parsed["tool_calls"] = tool_calls
            parsed["raw_response"] = content[:2000]
            return parsed
        # Try bare JSON
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(content[json_start:json_end])
            parsed["tool_calls"] = tool_calls
            parsed["raw_response"] = content[:2000]
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: extract from text
    return {
        "incident_summary": content[:500] if content else "No summary produced",
        "rca_ranked": _extract_rca(content),
        "recommended_action": _extract_action(content),
        "evidence_links": [],
        "tool_calls": tool_calls,
        "raw_response": content[:2000],
    }


def _extract_rca(text: str) -> list:
    candidates = []
    t = text.lower()
    if "cpu" in t and ("saturation" in t or "throttl" in t or "high" in t or "overload" in t):
        if "reviews-v2" in t or "reviews_v2" in t:
            candidates.append("bookinfo/reviews-v2:cpu_saturation")
        elif "reviews" in t:
            candidates.append("bookinfo/reviews:cpu_saturation")
        else:
            candidates.append("cpu_saturation")
    if "crashloop" in t or "crash" in t:
        candidates.append("crashloop_bad_config")
    if not candidates and "reviews" in t:
        candidates.append("bookinfo/reviews-v2:unknown")
    return candidates if candidates else ["unknown"]


def _extract_action(text: str) -> str:
    t = text.lower()
    if "scale" in t: return "Scale the affected deployment"
    if "restart" in t: return "Restart the affected pods"
    if "rollback" in t: return "Rollback the deployment"
    if "limit" in t and ("increase" in t or "raise" in t): return "Increase resource limits"
    return "Investigate and remediate manually"


def _fallback_output(error: str, tool_calls: list) -> dict:
    return {
        "incident_summary": f"Agent error: {error}",
        "rca_ranked": [], "recommended_action": "Manual investigation required",
        "evidence_links": [], "tool_calls": tool_calls, "error": error,
    }


# ---------------------------------------------------------------------------
# Scoring (same rubric as harness/runner/score.py)
# ---------------------------------------------------------------------------

WEIGHTS = {"detection": 0.10, "correlation": 0.10, "rca_detected": 0.05,
           "action_safety": 0.10, "auditability": 0.15, "rca_eval": 0.50}

UNSAFE_PATTERNS = [r"delete\s+namespace", r"drain\s+node", r"delete\s+deployment",
                   r"kubectl\s+delete\s+-A", r"oc\s+delete\s+project", r"rm\s+-rf"]
SAFE_PATTERNS = [r"scale\s+deployment", r"scale.*replicas", r"restart\s+pod",
                 r"rollout\s+restart", r"rollback", r"increase.*limit",
                 r"increase.*resource", r"fix.*config", r"correct.*env"]


def score_run(truth: dict, output: dict) -> dict:
    """Initial deterministic scoring (before judge eval is available)."""
    scores = {
        "detection": _score_detection(output),
        "correlation": _score_correlation(output),
        "rca_detected": _score_rca(output, truth),
        "action_safety": _score_action_safety(output),
        "auditability": _score_auditability(output),
        "rca_eval": 0.0,  # placeholder until judge matrix completes
    }
    weighted = round(sum(scores[k] * WEIGHTS[k] for k in scores), 4)
    rca_pass = scores["rca_detected"] == 1.0
    result = "PASS" if (weighted >= 0.60 and rca_pass) else "FAIL"
    return {"category_scores": scores, "weights": WEIGHTS,
            "weighted_score": weighted, "pass_threshold": 0.60, "result": result}


def rescore_with_eval(score: dict, judge_scores: dict) -> dict:
    """Recalculate weighted score after cross-model judge eval completes."""
    scores = dict(score["category_scores"])
    # Compute RCA Eval: average of peer judges' overall scores (normalized to 0-1)
    peer_overalls = [
        js["overall"] for js in judge_scores.values()
        if isinstance(js.get("overall"), (int, float))
    ]
    rca_eval = (sum(peer_overalls) / len(peer_overalls) / 10.0) if peer_overalls else 0.0
    scores["rca_eval"] = round(rca_eval, 4)
    weighted = round(sum(scores[k] * WEIGHTS[k] for k in scores), 4)
    rca_pass = scores["rca_detected"] == 1.0
    result = "PASS" if (weighted >= 0.60 and rca_pass) else "FAIL"
    return {"category_scores": scores, "weights": WEIGHTS,
            "weighted_score": weighted, "pass_threshold": 0.60, "result": result}


def _score_detection(o):
    s = 0.0
    if o.get("incident_summary", "") and len(o["incident_summary"]) > 20: s += 0.5
    if o.get("rca_ranked") and len(o["rca_ranked"]) > 0: s += 0.5
    return min(s, 1.0)


def _score_correlation(o):
    s = 0.0
    ev = o.get("evidence_links", [])
    tc = o.get("tool_calls", [])
    if len(ev) >= 2: s += 0.3
    elif len(ev) >= 1: s += 0.15
    # Count tool calls that actually returned useful data (not errors, not empty)
    successful_calls = 0
    for call in tc:
        summary = call.get("result_summary", "")
        if ('"status": "error"' not in summary
                and '"resultCount": 0' not in summary
                and '"results": []' not in summary):
            successful_calls += 1
    if successful_calls >= 3: s += 0.5
    elif successful_calls >= 1: s += 0.25
    # Bonus for breadth: using multiple distinct tool types
    tool_types = set(call.get("tool", "") for call in tc)
    if len(tool_types) >= 3: s += 0.2
    elif len(tool_types) >= 2: s += 0.1
    return min(s, 1.0)


def _score_rca(o, truth):
    """Binary RCA detection: 1.0 if root cause identified anywhere, 0.0 otherwise."""
    truth_label = truth.get("root_cause", {}).get("label", "")
    rca = o.get("rca_ranked", [])
    for h in rca:
        if _labels_match(h, truth_label):
            return 1.0
    if _text_mentions(o.get("incident_summary", ""), truth_label): return 1.0
    if _text_mentions(o.get("raw_response", ""), truth_label): return 1.0
    return 0.0


def _score_action_safety(o):
    action = o.get("recommended_action", "")
    if isinstance(action, list):
        action = " ".join(str(a) for a in action)
    if not action: return 0.5
    a = action.lower()
    for p in UNSAFE_PATTERNS:
        if re.search(p, a): return 0.0
    for p in SAFE_PATTERNS:
        if re.search(p, a): return 1.0
    return 0.7


def _score_auditability(o):
    s = 0.0
    if o.get("evidence_links"): s += 0.2
    tc = o.get("tool_calls", [])
    # Reward having tool calls that returned actual data
    successful = sum(1 for c in tc
                     if '"status": "error"' not in c.get("result_summary", "")
                     and '"resultCount": 0' not in c.get("result_summary", ""))
    if successful >= 3: s += 0.5
    elif successful >= 1: s += 0.3
    elif tc: s += 0.1  # made calls but all failed
    if o.get("incident_summary", "") and len(o["incident_summary"]) > 50: s += 0.3
    return min(s, 1.0)


def _labels_match(h, t):
    h = h.lower().replace("-", "_").replace(" ", "_")
    t = t.lower().replace("-", "_").replace(" ", "_")
    if h == t or t in h or h in t: return True
    hp = set(re.split(r'[/: _]', h))
    tp = set(re.split(r'[/: _]', t))
    # Extract fault-type tokens (after the last : or /)
    t_fault = set(re.split(r'[_]', t.split(":")[-1])) if ":" in t else tp
    # Require at least 1 fault-type token match AND 1 resource token match
    resource_match = len((hp - t_fault) & (tp - t_fault)) >= 1
    fault_match = len(hp & t_fault) >= 1
    return resource_match and fault_match


def _text_mentions(text, label):
    t = text.lower()
    parts = [p for p in re.split(r'[/: _-]', label.lower()) if len(p) > 2]
    return sum(1 for p in parts if p in t) >= 2


# ---------------------------------------------------------------------------
# Cross-model RCA judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """\
You are an expert SRE evaluating the quality of a Root Cause Analysis (RCA) \
produced by an AI agent investigating a Kubernetes incident.

You will receive:
1. The GROUND TRUTH — what actually caused the incident
2. The AGENT OUTPUT — the AI model's diagnosis, including its tool call log

Evaluate the RCA on these four criteria (score each 1–10):

- **rca_accuracy**: Does the top hypothesis correctly identify BOTH the right \
resource (e.g. reviews-v2, not productpage) AND the right fault type \
(e.g. cpu_saturation)? A hypothesis that names the correct fault but blames \
the wrong component should score 3–5, not 8–10.
- **evidence_quality**: Did the agent actually gather supporting evidence \
through its tool calls? Did it query the right metrics \
(container_cpu_usage_seconds_total), or did it guess from events alone? \
Penalize hallucinated evidence links or queries that returned errors/empty.
- **reasoning_coherence**: Does the incident summary logically follow from \
the tool results? Is the causal chain clear and traceable?
- **remediation_quality**: Is the recommended action specific, safe, and \
would it actually resolve the issue? "Remove the stress-injector" is better \
than "investigate further."

Respond ONLY with a JSON object (no markdown fences):
{"rca_accuracy": N, "evidence_quality": N, "reasoning_coherence": N, \
"remediation_quality": N, "overall": N, "justification": "one sentence"}

The "overall" score should be your holistic assessment (1–10), not a simple \
average. Weight rca_accuracy and evidence_quality most heavily.\
"""


def _format_judge_input(truth: dict, subject_output: dict) -> str:
    """Build the user message for the judge, containing ground truth + agent output."""
    # Summarize tool calls concisely
    tool_summary = []
    for tc in subject_output.get("tool_calls", []):
        tool = tc.get("tool", "?")
        args = tc.get("arguments", {})
        query = args.get("query", json.dumps(args)[:100]) if isinstance(args, dict) else str(args)[:100]
        summary = tc.get("result_summary", "")
        is_error = '"status": "error"' in summary
        is_empty = '"resultCount": 0' in summary or '"results": []' in summary
        status = "ERROR" if is_error else ("EMPTY" if is_empty else "DATA")
        tool_summary.append(f"  {tool}({query[:80]}) → {status}")

    return (
        f"GROUND TRUTH:\n"
        f"  Root cause: {truth['root_cause']['label']}\n"
        f"  Fault type: {truth['fault']['type']}\n"
        f"  Target: {truth['fault']['target']}\n\n"
        f"AGENT OUTPUT:\n"
        f"  Top RCA hypothesis: {subject_output.get('rca_ranked', ['(none)'])[0]}\n"
        f"  All hypotheses: {subject_output.get('rca_ranked', [])}\n"
        f"  Incident summary: {subject_output.get('incident_summary', '(none)')}\n"
        f"  Recommended action: {subject_output.get('recommended_action', '(none)')}\n"
        f"  Evidence links: {subject_output.get('evidence_links', [])}\n\n"
        f"TOOL CALL LOG ({len(subject_output.get('tool_calls', []))} calls):\n"
        + "\n".join(tool_summary)
    )


async def judge_rca(judge_key: str, judge_cfg: dict,
                    subject_key: str, subject_output: dict,
                    truth: dict) -> dict:
    """Have one model judge another model's RCA output."""
    user_msg = _format_judge_input(truth, subject_output)

    base_url = judge_cfg["base_url"]
    headers = {**judge_cfg["headers"], "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=120.0, verify=False) as c:
        try:
            resp = await c.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": judge_cfg["model_id"],
                    "messages": [
                        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 2048,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # Strip thinking tags (Qwen3 uses <think>...</think>)
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            # Strip markdown fences
            content = re.sub(r'```(?:json)?\s*', '', content).replace('```', '').strip()

            # Try to find and parse JSON — try progressively broader matches
            for pattern in [r'\{[^{}]*\}', r'\{.*?\}', r'\{.*\}']:
                json_match = re.search(pattern, content, re.DOTALL)
                if json_match:
                    try:
                        scores = json.loads(json_match.group())
                        if "overall" in scores or "rca_accuracy" in scores:
                            return scores
                    except json.JSONDecodeError:
                        continue

            # Try the full content as JSON
            try:
                scores = json.loads(content)
                return scores
            except json.JSONDecodeError:
                pass

            log.warning(f"[judge] {judge_key} → {subject_key}: could not parse JSON from: {content[:200]}")
            return {"error": "No valid JSON in response", "raw": content[:500]}
        except Exception as e:
            log.warning(f"[judge] {judge_key} → {subject_key} failed: {e}")
            return {"error": str(e)}


async def run_judge_matrix(results: dict, truth: dict) -> dict:
    """Run cross-evaluation: each model judges every other model's RCA.

    Returns {subject_key: {judge_key: scores_dict, ...}, ...}
    """
    judge_matrix = {mk: {} for mk in results}
    tasks = []

    for judge_key, judge_data in results.items():
        judge_cfg = MODELS[judge_key]
        for subject_key, subject_data in results.items():
            if judge_key == subject_key:
                continue  # don't self-evaluate
            tasks.append((
                judge_key, subject_key,
                judge_rca(judge_key, judge_cfg,
                          subject_key, subject_data["aiops_output"],
                          truth)
            ))

    # Run all judge calls concurrently
    for judge_key, subject_key, coro in tasks:
        log.info(f"[judge] {MODELS[judge_key]['name'].split('(')[0].strip()} evaluating "
                 f"{MODELS[subject_key]['name'].split('(')[0].strip()}...")
        result = await coro
        judge_matrix[subject_key][judge_key] = result
        overall = result.get("overall", "?")
        log.info(f"[judge]   → {overall}/10"
                 f" ({result.get('justification', 'no justification')[:80]})")

    return judge_matrix


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

async def run_benchmark():
    log.info("=" * 70)
    log.info("AIOps Harness — Local Benchmark: Granite vs. Granite+Lightspeed vs. Qwen3 vs. Qwen3+Lightspeed vs. Gemini")
    log.info("=" * 70)

    # --- Resolve endpoints ---
    # Granite: use OpenShift Route
    granite_route = subprocess.run(
        ["oc", "get", "route", "granite-4-server", "-n", "llm-serving",
         "-o", "jsonpath={.spec.host}"],
        capture_output=True, text=True,
    ).stdout.strip()
    MODELS["granite-4-tiny"]["base_url"] = f"https://{granite_route}/v1"
    MODELS["granite-4-tiny-lightspeed"]["base_url"] = f"https://{granite_route}/v1"
    log.info(f"Granite endpoint: {MODELS['granite-4-tiny']['base_url']}")

    # Qwen3-Coder-Next: use OpenShift Route
    qwen_route = subprocess.run(
        ["oc", "get", "route", "qwen3-coder-next", "-n", "llm-serving",
         "-o", "jsonpath={.spec.host}"],
        capture_output=True, text=True,
    ).stdout.strip()
    MODELS["qwen3-coder-next"]["base_url"] = f"https://{qwen_route}/v1"
    MODELS["qwen3-coder-next-lightspeed"]["base_url"] = f"https://{qwen_route}/v1"
    log.info(f"Qwen3 endpoint: {MODELS['qwen3-coder-next']['base_url']}")

    # Gemini: get API key from secret
    gemini_key = subprocess.run(
        ["oc", "get", "secret", "gemini-api-key", "-n", "llm-serving",
         "-o", "jsonpath={.data.GEMINI_API_KEY}"],
        capture_output=True, text=True,
    ).stdout.strip()
    import base64
    gemini_key = base64.b64decode(gemini_key).decode()
    MODELS["gemini-3-pro"]["headers"]["Authorization"] = f"Bearer {gemini_key}"

    # --- Check Bookinfo readiness & cleanup any leftover injection ---
    log.info("Checking Bookinfo pods...")
    load_k8s()
    apps_v1 = client.AppsV1Api()
    v1 = client.CoreV1Api()
    try:
        deploy = apps_v1.read_namespaced_deployment(DEPLOYMENT, NAMESPACE)
        container_names = [c.name for c in deploy.spec.template.spec.containers]
        if "stress-injector" in container_names:
            log.warning("Leftover stress-injector found — cleaning up before benchmark...")
            remove_cpu_saturation(NAMESPACE, DEPLOYMENT)
            log.info("Waiting 30s for clean pods to stabilize...")
            await asyncio.sleep(30)
    except Exception:
        pass
    pods = v1.list_namespaced_pod(namespace=NAMESPACE)
    running = [p.metadata.name for p in pods.items if p.status.phase == "Running"]
    log.info(f"Running pods: {running}")
    if len(running) < 4:
        log.warning(f"Only {len(running)} pods running. Some scenarios may have limited evidence.")

    # --- Verify Prometheus connectivity ---
    log.info("Verifying Prometheus access via Thanos...")
    test_metrics = await query_prometheus('up{namespace="bookinfo"}')
    log.info(f"Prometheus test: {test_metrics.get('resultCount', 0)} series found")

    # --- Ground truth ---
    truth = {
        "root_cause": {"label": "bookinfo/reviews-v2:cpu_saturation", "confidence": 1.0},
        "fault": {"type": "cpu_saturation", "target": "bookinfo/reviews-v2",
                  "parameters": {"cpuPercent": 95, "durationSeconds": 600}},
    }

    # --- Phase 1: Baseline ---
    log.info(f"\n{'='*60}")
    log.info(f"Phase 1: Baseline ({BASELINE_WAIT}s)")
    log.info(f"{'='*60}")
    baseline_start = datetime.now(timezone.utc)
    await asyncio.sleep(BASELINE_WAIT)

    # --- Phase 2: Inject ---
    log.info(f"\n{'='*60}")
    log.info("Phase 2: Inject CPU saturation into reviews-v2")
    log.info(f"{'='*60}")
    inject_start = datetime.now(timezone.utc)
    try:
        inject_cpu_saturation(NAMESPACE, DEPLOYMENT)
    except Exception as e:
        log.error(f"Injection failed: {e}")
        log.info("Continuing anyway — will benchmark with whatever evidence is available")

    # --- Phase 3: Wait for propagation ---
    log.info(f"\n{'='*60}")
    log.info(f"Phase 3: Waiting {INJECTION_WAIT}s for fault to propagate...")
    log.info(f"{'='*60}")
    await asyncio.sleep(INJECTION_WAIT)

    # --- Phase 4: Collect evidence ---
    log.info(f"\n{'='*60}")
    log.info("Phase 4: Collecting evidence")
    log.info(f"{'='*60}")
    evidence_end = datetime.now(timezone.utc)
    evidence_start = inject_start - timedelta(minutes=2)
    evidence = await collect_evidence(
        NAMESPACE, DEPLOYMENT,
        evidence_start.isoformat(), evidence_end.isoformat(),
    )
    log.info(f"Evidence collected: {len(evidence.get('metrics', {}))} metric types, "
             f"{len(evidence.get('events', []))} events, {len(evidence.get('logs', []))} log entries")

    # --- Phase 5: Invoke both models ---
    incident_time = datetime.now(timezone.utc).isoformat()
    window_start = (inject_start - timedelta(minutes=2)).isoformat()
    window_end = evidence_end.isoformat()
    incident_desc = (
        f"An operational incident has been detected in the 'bookinfo' namespace. "
        f"There are reports of service degradation affecting the Bookinfo application. "
        f"Users are experiencing increased latency and errors when accessing the product page. "
        f"Current UTC time: {incident_time}. "
        f"The incident window is approximately {window_start} to {window_end}. "
        f"Use these timestamps when querying metrics. "
        f"Please investigate using the available tools to determine the root cause."
    )

    results = {}
    for model_key, model_cfg in MODELS.items():
        log.info(f"\n{'='*60}")
        log.info(f"Phase 5: Invoking {model_cfg['name']}")
        log.info(f"{'='*60}")

        start_time = time.time()
        try:
            aiops_output = await invoke_agent(model_key, model_cfg, evidence, incident_desc)
        except Exception as e:
            log.error(f"[{model_key}] Agent failed: {e}")
            aiops_output = _fallback_output(str(e), [])
        elapsed = time.time() - start_time

        score = score_run(truth, aiops_output)

        results[model_key] = {
            "model": model_cfg["name"],
            "model_id": model_cfg["model_id"],
            "aiops_output": aiops_output,
            "score": score,
            "elapsed_seconds": round(elapsed, 2),
        }

        log.info(f"[{model_key}] Score: {score['weighted_score']} ({score['result']})")
        rca_status = "Detected" if score['category_scores']['rca_detected'] == 1.0 else "Not Detected"
        log.info(f"[{model_key}] RCA Detected: {rca_status}")
        log.info(f"[{model_key}] Time: {elapsed:.1f}s")

    # --- Phase 6: Cleanup ---
    log.info(f"\n{'='*60}")
    log.info("Phase 6: Removing fault injection")
    log.info(f"{'='*60}")
    try:
        remove_cpu_saturation(NAMESPACE, DEPLOYMENT)
    except Exception as e:
        log.warning(f"Cleanup failed: {e}")

    # --- Phase 7: Cross-Model RCA Judge ---
    log.info(f"\n{'='*60}")
    log.info("Phase 7: Cross-model RCA evaluation (each model judges the others)")
    log.info(f"{'='*60}")

    judge_matrix = await run_judge_matrix(results, truth)
    for mk in results:
        results[mk]["judge_scores"] = judge_matrix.get(mk, {})
        # Rescore with RCA Eval now that judge scores are available
        results[mk]["score"] = rescore_with_eval(
            results[mk]["score"], results[mk]["judge_scores"]
        )
        rca_eval = results[mk]["score"]["category_scores"]["rca_eval"]
        log.info(f"[{mk}] RCA Eval: {rca_eval:.2f} -> "
                 f"Final: {results[mk]['score']['weighted_score']} "
                 f"({results[mk]['score']['result']})")

    # --- Phase 8: Write results ---
    log.info(f"\n{'='*60}")
    log.info("Phase 8: Writing benchmark results")
    log.info(f"{'='*60}")

    output_dir = Path("artifacts/benchmark-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output_dir.mkdir(parents=True, exist_ok=True)

    for model_key, data in results.items():
        model_dir = output_dir / model_key
        model_dir.mkdir(exist_ok=True)
        for name, content in [
            ("aiops_output.json", data["aiops_output"]),
            ("score.json", data["score"]),
            ("truth.json", truth),
        ]:
            with open(model_dir / name, "w") as f:
                json.dump(content, f, indent=2, default=str)

    # Summary comparison
    summary = {"benchmark_time": datetime.now(timezone.utc).isoformat(), "models": {}}
    for mk, data in results.items():
        summary["models"][mk] = {
            "name": data["model"],
            "weighted_score": data["score"]["weighted_score"],
            "result": data["score"]["result"],
            "category_scores": data["score"]["category_scores"],
            "elapsed_seconds": data["elapsed_seconds"],
            "tool_calls_count": len(data["aiops_output"].get("tool_calls", [])),
            "rca_ranked": data["aiops_output"].get("rca_ranked", []),
            "judge_scores": data.get("judge_scores", {}),
        }
    with open(output_dir / "comparison.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Print comparison
    log.info(f"\nArtifacts written to: {output_dir}")

    # --- Final comparison (box-drawing tables) ---
    model_keys = [mk for mk in ["granite-4-tiny", "granite-4-tiny-lightspeed",
                                 "qwen3-coder-next", "qwen3-coder-next-lightspeed",
                                 "gemini-3-pro"]
                  if mk in results]

    MODEL_LABELS = {
        "granite-4-tiny": "Granite",
        "granite-4-tiny-lightspeed": "Granite + Lightspeed",
        "gemini-3-pro": "Gemini 3 Pro",
        "qwen3-coder-next": "Qwen3",
        "qwen3-coder-next-lightspeed": "Qwen3 + Lightspeed",
    }

    def _hallucination_check(judge_scores: dict) -> str:
        if not judge_scores:
            return "N/A"
        evidence_scores = []
        hallucinate_mentioned = False
        for js in judge_scores.values():
            if isinstance(js.get("evidence_quality"), (int, float)):
                evidence_scores.append(js["evidence_quality"])
            just = js.get("justification", "").lower()
            if "hallucinate" in just or "hallucinated" in just:
                hallucinate_mentioned = True
        avg_ev = sum(evidence_scores) / len(evidence_scores) if evidence_scores else 5
        if hallucinate_mentioned or avg_ev < 4:
            return "Yes"
        elif avg_ev < 7:
            return "Partially"
        return "No"

    def _box_table(col_defs, rows):
        """Render a box-drawing table. col_defs: [(header, key, width), ...]"""
        widths = [c[2] for c in col_defs]
        top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
        mid = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
        bot = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"
        hdr = "│" + "│".join(f" {c[0]:<{c[2]}} " for c in col_defs) + "│"
        lines = [top, hdr, mid]
        for i, row in enumerate(rows):
            line = "│" + "│".join(
                f" {str(row.get(c[1], '')):<{c[2]}} " for c in col_defs
            ) + "│"
            lines.append(line)
            if i < len(rows) - 1:
                lines.append(mid)
        lines.append(bot)
        return "\n".join(lines)

    # Build result rows
    table_rows = []
    for mk in model_keys:
        r = results[mk]
        s = r.get("score", {})
        cats = s.get("category_scores", {})
        js = r.get("judge_scores", {})
        rca_eval_raw = cats.get("rca_eval", 0)
        rca_eval_10 = rca_eval_raw * 10  # denormalize back to 1-10 scale
        table_rows.append({
            "model": MODEL_LABELS.get(mk, mk),
            "rca_detected": "Pass" if cats.get("rca_detected", 0) == 1.0 else "Fail",
            "score": f"{s.get('weighted_score', 0):.2f}",
            "rca_eval": f"{rca_eval_10:.1f}/10",
            "tool_calls": str(len(r.get("aiops_output", {}).get("tool_calls", []))),
            "hallucinated": _hallucination_check(js),
            "result": s.get("result", "?"),
            "time": f"{r.get('elapsed_seconds', 0):.1f}s",
        })

    cols = [
        ("Model",          "model",        22),
        ("RCA Detected",   "rca_detected", 13),
        ("Score",          "score",         7),
        ("RCA Eval",       "rca_eval",     10),
        ("Tool Calls",     "tool_calls",    10),
        ("Hallucinated?",  "hallucinated", 13),
        ("Result",         "result",        7),
        ("Time",           "time",          7),
    ]

    print("\n")
    print("BENCHMARK RESULTS")
    print(_box_table(cols, table_rows))

    # --- RCA Hypotheses ---
    print("\nRCA Hypotheses")
    for mk in model_keys:
        r = results.get(mk, {})
        print(f"\n  {MODEL_LABELS.get(mk, mk)}:")
        for i, h in enumerate(r.get("aiops_output", {}).get("rca_ranked", [])):
            print(f"    {i+1}. {h}")

    # --- RCA Eval Matrix (box-drawing) ---
    eval_col_w = 12
    eval_cols = [("Model", "model", 22)]
    for jk in model_keys:
        eval_cols.append((MODEL_LABELS.get(jk, jk)[:eval_col_w], jk, eval_col_w))
    eval_cols.append(("RCA Eval", "rca_eval", 9))

    eval_rows = []
    for sk in model_keys:
        row = {"model": MODEL_LABELS.get(sk, sk)}
        scores_list = []
        for jk in model_keys:
            if jk == sk:
                row[jk] = "--"
            else:
                js = results.get(sk, {}).get("judge_scores", {}).get(jk, {})
                overall = js.get("overall", None)
                if isinstance(overall, (int, float)):
                    scores_list.append(overall)
                    row[jk] = f"{overall:.0f}"
                else:
                    row[jk] = "err"
        avg = sum(scores_list) / len(scores_list) if scores_list else 0
        row["rca_eval"] = f"{avg:.1f}/10"
        eval_rows.append(row)

    print("\nCross-Model RCA Eval Matrix")
    print(_box_table(eval_cols, eval_rows))
    print("  (Each cell = row model's RCA scored by column model, 1-10 scale)")
    print("  (RCA Eval = average peer score, 50% of weighted total)")

    print(f"\nFull artifacts: {output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")  # suppress SSL warnings for dev
    asyncio.run(run_benchmark())
