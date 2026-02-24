#!/usr/bin/env python3
"""Distributed harness benchmark — staggered dual-fault cascade scenario.

Injects two independent faults with a 60-second stagger:
  1. T+0:  Bad config env var into ratings-v1 → CrashLoopBackOff
  2. T+60: CPU stress sidecar into reviews-v2 → CPU saturation

The agent must identify BOTH root causes and their temporal ordering.

Usage:
    python3 scripts/distributed_benchmark.py
"""

import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from kubernetes import client, config

# ---------------------------------------------------------------------------
# Import shared functions from local_benchmark.py
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))
from local_benchmark import (
    MODELS,
    TOOL_DEFINITIONS,
    RAG_TOOL_DEFINITION,
    WEIGHTS,
    query_prometheus,
    get_k8s_events,
    search_pod_logs,
    search_documentation,
    inject_cpu_saturation,
    remove_cpu_saturation,
    _c2d,
    _extract_action,
    _fallback_output,
    _labels_match,
    _text_mentions,
    _score_detection,
    _score_correlation,
    _score_action_safety,
    _score_auditability,
    _hallucination_check,
    _box_table,
    load_k8s,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("distributed-benchmark")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NAMESPACE = "bookinfo"
RATINGS_DEPLOYMENT = "ratings-v1"
REVIEWS_DEPLOYMENT = "reviews-v2"

BASELINE_WAIT = 30        # seconds
STAGGER_WAIT = 60         # delay between fault #1 and fault #2
CASCADE_WAIT = 120        # time for both faults to propagate

# MLFlow experiment tracking
from mlflow_utils import (
    get_mlflow_aiops_url, get_mlflow_harness_url,
    log_distributed_run, log_harness_eval,
)
MLFLOW_AIOPS_URL = get_mlflow_aiops_url()
MLFLOW_HARNESS_URL = get_mlflow_harness_url()

# ---------------------------------------------------------------------------
# Fault injection: bad config env var (CrashLoopBackOff)
# ---------------------------------------------------------------------------

def inject_bad_config(namespace: str, deployment_name: str,
                      env_var: str = "INVALID_DB_HOST",
                      env_value: str = "this-host-does-not-exist.invalid"):
    """Inject a bad environment variable and override entrypoint to cause CrashLoopBackOff.

    Patches the deployment to add the invalid env var AND replaces the command
    with one that immediately exits with an error, simulating a config-related crash.
    """
    load_k8s()
    apps_v1 = client.AppsV1Api()
    deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    containers = deploy.spec.template.spec.containers

    patched = []
    for c in containers:
        d = _c2d(c)
        # Inject bad env var
        envs = d.get("env", [])
        envs.append({"name": env_var, "value": env_value})
        d["env"] = envs
        # Override command to exit immediately (simulates crash on bad config)
        d["command"] = ["sh", "-c"]
        d["args"] = [f'echo "FATAL: Cannot connect to ${env_var}" >&2; exit 1']
        patched.append(d)

    patch_json = json.dumps({"spec": {"template": {"spec": {"containers": patched}}}})
    result = subprocess.run(
        ["oc", "patch", "deployment", deployment_name, "-n", namespace,
         "--type=merge", "-p", patch_json],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"oc patch failed: {result.stderr}")
    log.info(f"Injected bad config ({env_var}={env_value}) into {namespace}/{deployment_name}")


def remove_bad_config(namespace: str, deployment_name: str):
    """Remove the bad config injection by rolling back the deployment.

    Uses oc rollout undo to revert to the previous revision, which restores
    the original command/args and removes the injected env var.
    """
    result = subprocess.run(
        ["oc", "rollout", "undo", f"deployment/{deployment_name}", "-n", namespace],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"oc rollout undo failed: {result.stderr}")
    log.info(f"Rolled back {namespace}/{deployment_name} to remove bad config")


# ---------------------------------------------------------------------------
# Node topology tool
# ---------------------------------------------------------------------------

TOPOLOGY_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "getNodeTopology",
        "description": (
            "Get the node-to-pod mapping for a namespace. Shows which pods "
            "are running on which nodes, including pod status, container names, "
            "and restart counts. Use this to understand the physical topology "
            "of the distributed system and identify which nodes are affected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to inspect",
                    "default": "bookinfo",
                },
            },
        },
    },
}


def get_node_topology(namespace: str = "bookinfo") -> dict:
    """Return node-to-pod mapping for a namespace."""
    load_k8s()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace=namespace)
    topology = {}
    for pod in pods.items:
        node = pod.spec.node_name or "unscheduled"
        if node not in topology:
            topology[node] = []
        restarts = 0
        if pod.status.container_statuses:
            restarts = sum(cs.restart_count for cs in pod.status.container_statuses)
        topology[node].append({
            "pod": pod.metadata.name,
            "status": pod.status.phase,
            "containers": [c.name for c in pod.spec.containers],
            "restarts": restarts,
        })
    return topology


# ---------------------------------------------------------------------------
# Extended tool execution (adds getNodeTopology)
# ---------------------------------------------------------------------------

async def execute_tool_call(tool_name: str, args: dict) -> dict:
    """Execute a tool call, extending local_benchmark with getNodeTopology."""
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
        elif tool_name == "getNodeTopology":
            return {"topology": get_node_topology(
                args.get("namespace", "bookinfo"),
            )}
        else:
            return {"status": "not_configured", "message": f"Tool {tool_name} not available"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:500]}


# ---------------------------------------------------------------------------
# System prompts (extended for distributed investigation)
# ---------------------------------------------------------------------------

DISTRIBUTED_SYSTEM_PROMPT = (
    "You are an expert SRE AI agent investigating an operational incident "
    "in a Kubernetes-based microservices application called Bookinfo. "
    "Services: productpage (frontend), details, reviews (v1, v2, v3), ratings. "
    "Dependency chain: productpage -> reviews -> ratings. "
    "\n\n"
    "IMPORTANT: This incident may involve MULTIPLE independent root causes "
    "affecting different services simultaneously. Investigate each service "
    "independently and consider cross-service dependencies. Report ALL root "
    "causes you find, not just the first one. Pay attention to timestamps to "
    "determine the order of fault onset. "
    "\n\n"
    "You MUST use the available tools to gather evidence before drawing conclusions. "
    "Do not guess. Query metrics, check events, search logs, and examine "
    "the node topology to build your case. "
    "\n\n"
    "After investigation, provide your findings as JSON with keys: "
    "incident_summary, rca_ranked (list of ALL root causes found, e.g. "
    "'bookinfo/ratings-v1:crashloop_bad_config', "
    "'bookinfo/reviews-v2:cpu_saturation'), "
    "recommended_action (list of actions, one per root cause), "
    "evidence_links (list of strings referencing specific "
    "metrics or events you discovered), "
    "temporal_analysis (describe the order of events and any cascade effects)."
)

DISTRIBUTED_RAG_SYSTEM_PROMPT = (
    DISTRIBUTED_SYSTEM_PROMPT + "\n\n"
    "You have access to a searchDocumentation tool containing curated OpenShift "
    "and Kubernetes documentation (powered by OpenShift Lightspeed). If you are "
    "unsure about the correct PromQL metric name or query syntax, make ONE quick "
    "documentation search first, then immediately move on to querying live systems. "
    "Do NOT spend more than one tool call on documentation. Your primary job is "
    "to investigate the actual incident using getMetricHistory, getK8sEvents, "
    "searchLogs, and getNodeTopology."
)


# ---------------------------------------------------------------------------
# Agent invocation (adapted for distributed scenario)
# ---------------------------------------------------------------------------

async def invoke_agent(model_key: str, model_cfg: dict, evidence: dict,
                       incident_desc: str) -> dict:
    """Invoke LLM with tool-calling for distributed RCA investigation."""
    rag_enabled = model_cfg.get("rag_enabled", False)
    system_prompt = DISTRIBUTED_RAG_SYSTEM_PROMPT if rag_enabled else DISTRIBUTED_SYSTEM_PROMPT
    tools = (TOOL_DEFINITIONS + [TOPOLOGY_TOOL_DEFINITION]
             + ([RAG_TOOL_DEFINITION] if rag_enabled else []))

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"INCIDENT ALERT:\n{incident_desc}\n\n"
                "Use the available tools to investigate this incident. "
                "Query Prometheus metrics, check Kubernetes events, search pod logs, "
                "and examine node topology to determine ALL root causes. "
                "This may involve multiple faults affecting different services. "
                "Provide your root cause analysis as JSON."
            ),
        },
    ]

    tool_calls_log = []
    base_url = model_cfg["base_url"]
    headers = {**model_cfg["headers"], "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300.0, verify=False) as c:
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

        # Tool-calling loop (up to 5 rounds for distributed — more tools needed)
        max_rounds = 5
        round_num = 0
        while message.get("tool_calls") and round_num < max_rounds:
            round_num += 1
            log.info(f"[{model_key}] Processing {len(message['tool_calls'])} tool call(s) (round {round_num})...")
            messages.append(message)

            for tc in message["tool_calls"]:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    raw_args = fn.get("arguments", "{}")
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

        # Final text-only call if content is empty
        content = message.get("content", "")
        if not content or len(content) < 10:
            log.info(f"[{model_key}] Empty response after tool rounds, making final text-only call...")
            messages.append({"role": "user", "content": (
                "Based on all the tool results above, please provide your final root cause analysis "
                "as a JSON object with keys: incident_summary, rca_ranked (list of ALL root causes "
                "found, e.g. 'bookinfo/ratings-v1:crashloop_bad_config', "
                "'bookinfo/reviews-v2:cpu_saturation'), recommended_action, evidence_links, "
                "temporal_analysis."
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
        return _parse_distributed_response(content, tool_calls_log)


def _parse_distributed_response(content: str, tool_calls: list) -> dict:
    """Parse LLM response, extending _extract_rca for multi-cause detection."""
    # Try JSON extraction first (same as local_benchmark)
    try:
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(1))
            parsed["tool_calls"] = tool_calls
            parsed["raw_response"] = content[:2000]
            return parsed
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(content[json_start:json_end])
            parsed["tool_calls"] = tool_calls
            parsed["raw_response"] = content[:2000]
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: extract from text with multi-cause awareness
    return {
        "incident_summary": content[:500] if content else "No summary produced",
        "rca_ranked": _extract_distributed_rca(content),
        "recommended_action": _extract_action(content),
        "evidence_links": [],
        "temporal_analysis": "",
        "tool_calls": tool_calls,
        "raw_response": content[:2000],
    }


def _extract_distributed_rca(text: str) -> list:
    """Extract RCA candidates with multi-cause awareness."""
    candidates = []
    t = text.lower()

    # Check for ratings-v1 CrashLoopBackOff
    if ("crashloop" in t or "crash" in t or "crashloopbackoff" in t) and ("ratings" in t):
        candidates.append("bookinfo/ratings-v1:crashloop_bad_config")
    elif "crashloop" in t or "crashloopbackoff" in t:
        candidates.append("crashloop_bad_config")

    # Check for reviews-v2 CPU saturation
    if "cpu" in t and ("saturation" in t or "throttl" in t or "high" in t or "stress" in t or "overload" in t):
        if "reviews-v2" in t or "reviews_v2" in t:
            candidates.append("bookinfo/reviews-v2:cpu_saturation")
        elif "reviews" in t:
            candidates.append("bookinfo/reviews:cpu_saturation")
        else:
            candidates.append("cpu_saturation")

    # Check for bad config / env var
    if ("bad" in t and "config" in t) or ("invalid" in t and ("env" in t or "host" in t or "db" in t)):
        if "ratings" in t and "bookinfo/ratings-v1:crashloop_bad_config" not in candidates:
            candidates.append("bookinfo/ratings-v1:crashloop_bad_config")

    if not candidates:
        if "ratings" in t:
            candidates.append("bookinfo/ratings-v1:unknown")
        if "reviews" in t:
            candidates.append("bookinfo/reviews-v2:unknown")
    return candidates if candidates else ["unknown"]


# ---------------------------------------------------------------------------
# Multi-cause scoring
# ---------------------------------------------------------------------------

def _score_rca_multi(output: dict, truth: dict) -> float:
    """Multi-cause RCA detection: 1.0 if all causes found, 0.5 if partial, 0.0 if none."""
    root_causes = truth.get("root_causes", [])
    if not root_causes:
        # Fallback to single root cause
        label = truth.get("root_cause", {}).get("label", "")
        rca = output.get("rca_ranked", [])
        for h in rca:
            if _labels_match(h, label):
                return 1.0
        if _text_mentions(output.get("raw_response", ""), label):
            return 1.0
        return 0.0

    found = 0
    for rc in root_causes:
        label = rc["label"]
        # Check rca_ranked list
        for h in output.get("rca_ranked", []):
            if _labels_match(h, label):
                found += 1
                break
        else:
            # Check raw response text
            if _text_mentions(output.get("incident_summary", ""), label):
                found += 1
            elif _text_mentions(output.get("raw_response", ""), label):
                found += 1

    if found == len(root_causes):
        return 1.0
    elif found > 0:
        return 0.5
    return 0.0


def _rca_detected_binary(output: dict, truth: dict) -> float:
    """Binary gate: 1.0 if at least ONE root cause identified, 0.0 otherwise."""
    root_causes = truth.get("root_causes", [])
    if not root_causes:
        label = truth.get("root_cause", {}).get("label", "")
        root_causes = [{"label": label}]

    for rc in root_causes:
        label = rc["label"]
        for h in output.get("rca_ranked", []):
            if _labels_match(h, label):
                return 1.0
        if _text_mentions(output.get("incident_summary", ""), label):
            return 1.0
        if _text_mentions(output.get("raw_response", ""), label):
            return 1.0
    return 0.0


def score_run(truth: dict, output: dict) -> dict:
    """Score a distributed scenario run with multi-cause awareness."""
    rca_multi = _score_rca_multi(output, truth)
    rca_detected = _rca_detected_binary(output, truth)
    causes_found = _count_causes_found(output, truth)
    total_causes = len(truth.get("root_causes", []))

    scores = {
        "detection": _score_detection(output),
        "correlation": _score_correlation(output),
        "rca_detected": rca_detected,
        "action_safety": _score_action_safety(output),
        "auditability": _score_auditability(output),
        "rca_eval": 0.0,  # placeholder until judge matrix completes
    }
    weighted = round(sum(scores[k] * WEIGHTS[k] for k in scores), 4)
    rca_pass = rca_detected == 1.0
    result = "PASS" if (weighted >= 0.60 and rca_pass) else "FAIL"
    return {
        "category_scores": scores,
        "weights": WEIGHTS,
        "weighted_score": weighted,
        "pass_threshold": 0.60,
        "result": result,
        "multi_cause": {
            "rca_completeness": rca_multi,
            "causes_found": causes_found,
            "total_causes": total_causes,
        },
    }


def _count_causes_found(output: dict, truth: dict) -> int:
    """Count how many root causes were found."""
    root_causes = truth.get("root_causes", [])
    found = 0
    for rc in root_causes:
        label = rc["label"]
        for h in output.get("rca_ranked", []):
            if _labels_match(h, label):
                found += 1
                break
        else:
            if (_text_mentions(output.get("incident_summary", ""), label)
                    or _text_mentions(output.get("raw_response", ""), label)):
                found += 1
    return found


def rescore_with_eval(score: dict, judge_scores: dict) -> dict:
    """Recalculate weighted score after cross-model judge eval completes."""
    scores = dict(score["category_scores"])
    peer_overalls = [
        js["overall"] for js in judge_scores.values()
        if isinstance(js.get("overall"), (int, float))
    ]
    rca_eval = (sum(peer_overalls) / len(peer_overalls) / 10.0) if peer_overalls else 0.0
    scores["rca_eval"] = round(rca_eval, 4)
    weighted = round(sum(scores[k] * WEIGHTS[k] for k in scores), 4)
    rca_pass = scores["rca_detected"] == 1.0
    result = "PASS" if (weighted >= 0.60 and rca_pass) else "FAIL"
    return {
        "category_scores": scores,
        "weights": WEIGHTS,
        "weighted_score": weighted,
        "pass_threshold": 0.60,
        "result": result,
        "multi_cause": score.get("multi_cause", {}),
    }


# ---------------------------------------------------------------------------
# Cross-model RCA judge (extended for multi-cause)
# ---------------------------------------------------------------------------

DISTRIBUTED_JUDGE_SYSTEM_PROMPT = """\
You are an expert SRE evaluating the quality of a Root Cause Analysis (RCA) \
produced by an AI agent investigating a DISTRIBUTED Kubernetes incident \
involving MULTIPLE independent root causes.

You will receive:
1. The GROUND TRUTH — what actually caused the incident (MULTIPLE root causes)
2. The AGENT OUTPUT — the AI model's diagnosis, including its tool call log

Evaluate the RCA on these four criteria (score each 1-10):

- **rca_accuracy**: Did the agent identify ALL root causes? The ground truth \
contains MULTIPLE independent faults. Score 9-10 if both causes found with \
correct resource and fault type. Score 5-7 if only one cause found. Score 1-4 \
if neither or wrong resources identified. Penalize conflating two independent \
faults into a single cause.
- **evidence_quality**: Did the agent investigate BOTH affected services \
through tool calls? Did it query metrics for BOTH ratings and reviews? \
Did it check node topology? Penalize if investigation focused on only \
one service.
- **reasoning_coherence**: Does the analysis correctly identify the temporal \
ordering (which fault came first)? Does it distinguish between direct faults \
and cascade effects? Is the causal chain clear?
- **remediation_quality**: Are the recommended actions specific to EACH root \
cause? Does it suggest fixing the config issue AND addressing the CPU \
saturation? Are actions safe?

Respond ONLY with a JSON object (no markdown fences):
{"rca_accuracy": N, "evidence_quality": N, "reasoning_coherence": N, \
"remediation_quality": N, "overall": N, "justification": "one sentence"}

The "overall" score should be your holistic assessment (1-10), not a simple \
average. Weight rca_accuracy and evidence_quality most heavily.\
"""


def _format_distributed_judge_input(truth: dict, subject_output: dict) -> str:
    """Build the user message for the judge, showing multi-cause ground truth."""
    root_causes = truth.get("root_causes", [])
    rc_lines = []
    for rc in root_causes:
        rc_lines.append(f"    #{rc['order']}: {rc['label']} (injected at T+{rc['inject_offset_seconds']}s)")

    tool_summary = []
    for tc in subject_output.get("tool_calls", []):
        tool = tc.get("tool", "?")
        args = tc.get("arguments", {})
        query = args.get("query", json.dumps(args)[:100]) if isinstance(args, dict) else str(args)[:100]
        summary = tc.get("result_summary", "")
        is_error = '"status": "error"' in summary
        is_empty = '"resultCount": 0' in summary or '"results": []' in summary
        status = "ERROR" if is_error else ("EMPTY" if is_empty else "DATA")
        tool_summary.append(f"  {tool}({query[:80]}) -> {status}")

    return (
        f"GROUND TRUTH (MULTIPLE ROOT CAUSES):\n"
        f"  Fault type: {truth['fault']['type']}\n"
        f"  Targets: {truth['fault']['targets']}\n"
        f"  Stagger: {truth['fault'].get('stagger_seconds', 0)} seconds between faults\n"
        f"  Root causes (in order of injection):\n"
        + "\n".join(rc_lines) + "\n\n"
        f"AGENT OUTPUT:\n"
        f"  RCA hypotheses: {subject_output.get('rca_ranked', [])}\n"
        f"  Incident summary: {subject_output.get('incident_summary', '(none)')}\n"
        f"  Recommended action: {subject_output.get('recommended_action', '(none)')}\n"
        f"  Temporal analysis: {subject_output.get('temporal_analysis', '(none)')}\n"
        f"  Evidence links: {subject_output.get('evidence_links', [])}\n\n"
        f"TOOL CALL LOG ({len(subject_output.get('tool_calls', []))} calls):\n"
        + "\n".join(tool_summary)
    )


async def judge_rca(judge_key: str, judge_cfg: dict,
                    subject_key: str, subject_output: dict,
                    truth: dict) -> dict:
    """Have one model judge another model's distributed RCA output."""
    user_msg = _format_distributed_judge_input(truth, subject_output)

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
                        {"role": "system", "content": DISTRIBUTED_JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 2048,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # Strip thinking tags and markdown fences
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            content = re.sub(r'```(?:json)?\s*', '', content).replace('```', '').strip()

            for pattern in [r'\{[^{}]*\}', r'\{.*?\}', r'\{.*\}']:
                json_match = re.search(pattern, content, re.DOTALL)
                if json_match:
                    try:
                        scores = json.loads(json_match.group())
                        if "overall" in scores or "rca_accuracy" in scores:
                            return scores
                    except json.JSONDecodeError:
                        continue

            try:
                scores = json.loads(content)
                return scores
            except json.JSONDecodeError:
                pass

            log.warning(f"[judge] {judge_key} -> {subject_key}: could not parse JSON from: {content[:200]}")
            return {"error": "No valid JSON in response", "raw": content[:500]}
        except Exception as e:
            log.warning(f"[judge] {judge_key} -> {subject_key} failed: {e}")
            return {"error": str(e)}


async def run_judge_matrix(results: dict, truth: dict) -> dict:
    """Run cross-evaluation for the distributed scenario."""
    judge_matrix = {mk: {} for mk in results}
    tasks = []

    for judge_key, judge_data in results.items():
        judge_cfg = MODELS[judge_key]
        for subject_key, subject_data in results.items():
            if judge_key == subject_key:
                continue
            tasks.append((
                judge_key, subject_key,
                judge_rca(judge_key, judge_cfg,
                          subject_key, subject_data["aiops_output"],
                          truth)
            ))

    for jk, sk, coro in tasks:
        log.info(f"[judge] {MODELS[jk]['name'].split('(')[0].strip()} evaluating "
                 f"{MODELS[sk]['name'].split('(')[0].strip()}...")
        result = await coro
        judge_matrix[sk][jk] = result
        overall = result.get("overall", "?")
        log.info(f"[judge]   -> {overall}/10"
                 f" ({result.get('justification', 'no justification')[:80]})")

    return judge_matrix


# ---------------------------------------------------------------------------
# Evidence collection (extended for distributed scenario)
# ---------------------------------------------------------------------------

async def collect_evidence(namespace: str,
                           start_time: str, end_time: str) -> dict:
    """Collect evidence covering both fault targets."""
    evidence = {
        "collection_time": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start_time, "end": end_time},
        "metrics": {}, "events": [], "logs": [], "topology": {},
    }

    # Ratings-v1 signals
    evidence["metrics"]["ratings_restarts"] = await query_prometheus(
        f'kube_pod_container_status_restarts_total{{namespace="{namespace}", pod=~"ratings-v1.*"}}',
    )
    evidence["metrics"]["ratings_waiting"] = await query_prometheus(
        f'kube_pod_container_status_waiting_reason{{namespace="{namespace}", pod=~"ratings-v1.*"}}',
    )

    # Reviews-v2 signals
    evidence["metrics"]["reviews_cpu"] = await query_prometheus(
        f'rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod=~"reviews-v2.*"}}[5m])',
        start_time, end_time,
    )
    evidence["metrics"]["reviews_memory"] = await query_prometheus(
        f'container_memory_working_set_bytes{{namespace="{namespace}", pod=~"reviews-v2.*"}}',
        start_time, end_time,
    )

    # Cross-service signals
    evidence["metrics"]["all_restarts"] = await query_prometheus(
        f'kube_pod_container_status_restarts_total{{namespace="{namespace}"}}',
    )
    evidence["metrics"]["all_pod_status"] = await query_prometheus(
        f'kube_pod_status_phase{{namespace="{namespace}"}}',
    )

    evidence["events"] = get_k8s_events(namespace, since_minutes=30)
    evidence["logs"] = search_pod_logs(namespace, "error", 50)
    evidence["topology"] = get_node_topology(namespace)

    return evidence


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

async def run_benchmark():
    log.info("=" * 70)
    log.info("AIOps Harness — Distributed Benchmark: Staggered Dual-Fault Cascade")
    log.info("  Fault #1: CrashLoopBackOff on ratings-v1 (T+0)")
    log.info("  Fault #2: CPU saturation on reviews-v2 (T+60)")
    log.info("=" * 70)

    # --- Resolve endpoints (same as local_benchmark) ---
    import base64

    granite_route = subprocess.run(
        ["oc", "get", "route", "granite-4-server", "-n", "llm-serving",
         "-o", "jsonpath={.spec.host}"],
        capture_output=True, text=True,
    ).stdout.strip()
    MODELS["granite-4-tiny"]["base_url"] = f"https://{granite_route}/v1"
    MODELS["granite-4-tiny-lightspeed"]["base_url"] = f"https://{granite_route}/v1"
    log.info(f"Granite endpoint: {MODELS['granite-4-tiny']['base_url']}")

    qwen_route = subprocess.run(
        ["oc", "get", "route", "qwen3-coder-next", "-n", "llm-serving",
         "-o", "jsonpath={.spec.host}"],
        capture_output=True, text=True,
    ).stdout.strip()
    MODELS["qwen3-coder-next"]["base_url"] = f"https://{qwen_route}/v1"
    MODELS["qwen3-coder-next-lightspeed"]["base_url"] = f"https://{qwen_route}/v1"
    log.info(f"Qwen3 endpoint: {MODELS['qwen3-coder-next']['base_url']}")

    gemini_key = subprocess.run(
        ["oc", "get", "secret", "gemini-api-key", "-n", "llm-serving",
         "-o", "jsonpath={.data.GEMINI_API_KEY}"],
        capture_output=True, text=True,
    ).stdout.strip()
    gemini_key = base64.b64decode(gemini_key).decode()
    MODELS["gemini-3-pro"]["headers"]["Authorization"] = f"Bearer {gemini_key}"

    # --- Check Bookinfo readiness & cleanup any leftovers ---
    log.info("Checking Bookinfo pods and cleaning up any leftover injections...")
    load_k8s()
    apps_v1 = client.AppsV1Api()
    v1 = client.CoreV1Api()

    # Cleanup leftover CPU saturation on reviews-v2
    try:
        deploy = apps_v1.read_namespaced_deployment(REVIEWS_DEPLOYMENT, NAMESPACE)
        container_names = [c.name for c in deploy.spec.template.spec.containers]
        if "stress-injector" in container_names:
            log.warning("Leftover stress-injector on reviews-v2 — cleaning up...")
            remove_cpu_saturation(NAMESPACE, REVIEWS_DEPLOYMENT)
            await asyncio.sleep(15)
    except Exception:
        pass

    # Check ratings-v1 is healthy
    try:
        deploy = apps_v1.read_namespaced_deployment(RATINGS_DEPLOYMENT, NAMESPACE)
        container_names = [c.name for c in deploy.spec.template.spec.containers]
        # Check if ratings has the bad config (command override)
        for c in deploy.spec.template.spec.containers:
            if c.command and "exit 1" in " ".join(c.args or []):
                log.warning("Leftover bad config on ratings-v1 — rolling back...")
                remove_bad_config(NAMESPACE, RATINGS_DEPLOYMENT)
                await asyncio.sleep(15)
                break
    except Exception:
        pass

    pods = v1.list_namespaced_pod(namespace=NAMESPACE)
    running = [p.metadata.name for p in pods.items if p.status.phase == "Running"]
    log.info(f"Running pods: {running}")

    # --- Verify Prometheus connectivity ---
    log.info("Verifying Prometheus access via Thanos...")
    test_metrics = await query_prometheus('up{namespace="bookinfo"}')
    log.info(f"Prometheus test: {test_metrics.get('resultCount', 0)} series found")

    # --- Ground truth ---
    truth = {
        "root_cause": {
            "label": "bookinfo/ratings-v1:crashloop_bad_config",
            "confidence": 1.0,
        },
        "root_causes": [
            {
                "label": "bookinfo/ratings-v1:crashloop_bad_config",
                "order": 1,
                "inject_offset_seconds": 0,
                "confidence": 1.0,
            },
            {
                "label": "bookinfo/reviews-v2:cpu_saturation",
                "order": 2,
                "inject_offset_seconds": 60,
                "confidence": 1.0,
            },
        ],
        "fault": {
            "type": "distributed_cascading_failure",
            "targets": ["bookinfo/ratings-v1", "bookinfo/reviews-v2"],
            "stagger_seconds": 60,
        },
    }

    # --- Phase 1: Baseline ---
    log.info(f"\n{'='*60}")
    log.info(f"Phase 1: Baseline ({BASELINE_WAIT}s)")
    log.info(f"{'='*60}")
    baseline_start = datetime.now(timezone.utc)
    await asyncio.sleep(BASELINE_WAIT)

    # --- Phase 2: Inject fault #1 — bad config into ratings-v1 ---
    log.info(f"\n{'='*60}")
    log.info("Phase 2: Inject fault #1 — bad config into ratings-v1 (CrashLoopBackOff)")
    log.info(f"{'='*60}")
    fault1_time = datetime.now(timezone.utc)
    try:
        inject_bad_config(NAMESPACE, RATINGS_DEPLOYMENT)
    except Exception as e:
        log.error(f"Fault #1 injection failed: {e}")
        log.info("Continuing anyway...")

    # --- Phase 3: Wait for first fault to propagate ---
    log.info(f"\n{'='*60}")
    log.info(f"Phase 3: Waiting {STAGGER_WAIT}s for fault #1 to propagate...")
    log.info(f"{'='*60}")
    await asyncio.sleep(STAGGER_WAIT)

    # --- Phase 4: Inject fault #2 — CPU saturation into reviews-v2 ---
    log.info(f"\n{'='*60}")
    log.info("Phase 4: Inject fault #2 — CPU saturation into reviews-v2")
    log.info(f"{'='*60}")
    fault2_time = datetime.now(timezone.utc)
    try:
        inject_cpu_saturation(NAMESPACE, REVIEWS_DEPLOYMENT)
    except Exception as e:
        log.error(f"Fault #2 injection failed: {e}")
        log.info("Continuing anyway...")

    # --- Phase 5: Wait for cascade to develop ---
    log.info(f"\n{'='*60}")
    log.info(f"Phase 5: Waiting {CASCADE_WAIT}s for cascade to develop (both faults active)...")
    log.info(f"{'='*60}")
    await asyncio.sleep(CASCADE_WAIT)

    # --- Phase 6: Collect evidence ---
    log.info(f"\n{'='*60}")
    log.info("Phase 6: Collecting evidence (both faults now active)")
    log.info(f"{'='*60}")
    evidence_end = datetime.now(timezone.utc)
    evidence_start = fault1_time - timedelta(minutes=2)
    evidence = await collect_evidence(
        NAMESPACE,
        evidence_start.isoformat(), evidence_end.isoformat(),
    )
    log.info(f"Evidence collected: {len(evidence.get('metrics', {}))} metric types, "
             f"{len(evidence.get('events', []))} events, {len(evidence.get('logs', []))} log entries")

    # --- Phase 7: Invoke models ---
    incident_time = datetime.now(timezone.utc).isoformat()
    window_start = (fault1_time - timedelta(minutes=2)).isoformat()
    window_end = evidence_end.isoformat()
    incident_desc = (
        f"Multiple services in the 'bookinfo' namespace are experiencing degradation. "
        f"Users report intermittent errors and increased latency when accessing the "
        f"product page. The issues appear to have started approximately "
        f"{fault1_time.strftime('%H:%M:%S UTC')} and worsened around "
        f"{fault2_time.strftime('%H:%M:%S UTC')}. Some services may be affected differently. "
        f"Current UTC time: {incident_time}. "
        f"Investigation window: {window_start} to {window_end}. "
        f"Use these timestamps when querying metrics. "
        f"Investigate using the available tools to determine the root cause(s)."
    )

    results = {}
    for model_key, model_cfg in MODELS.items():
        log.info(f"\n{'='*60}")
        log.info(f"Phase 7: Invoking {model_cfg['name']}")
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

        mc = score.get("multi_cause", {})
        log.info(f"[{model_key}] Score: {score['weighted_score']} ({score['result']})")
        log.info(f"[{model_key}] Causes found: {mc.get('causes_found', 0)}/{mc.get('total_causes', 0)}")
        log.info(f"[{model_key}] RCA Completeness: {mc.get('rca_completeness', 0)}")
        log.info(f"[{model_key}] Time: {elapsed:.1f}s")

        # Log to MLFlow AIOps (distributed investigation tracking)
        log_distributed_run(
            model_id=model_cfg["model_id"],
            scenario="distributed-cascading-multi-service",
            tool_calls=aiops_output.get("tool_calls", []),
            rca_output=aiops_output,
            investigation_time_seconds=elapsed,
            causes_found=mc.get("causes_found", 0),
            total_causes=mc.get("total_causes", 0),
            rca_completeness=mc.get("rca_completeness", 0.0),
            fault1_time=fault1_time.isoformat(),
            fault2_time=fault2_time.isoformat(),
            stagger_seconds=STAGGER_WAIT,
            mlflow_url=MLFLOW_AIOPS_URL,
        )

    # --- Phase 8: Cleanup (reverse order) ---
    log.info(f"\n{'='*60}")
    log.info("Phase 8: Removing fault injections (reverse order)")
    log.info(f"{'='*60}")
    try:
        remove_cpu_saturation(NAMESPACE, REVIEWS_DEPLOYMENT)
        log.info("Removed CPU saturation from reviews-v2")
    except Exception as e:
        log.warning(f"CPU cleanup failed: {e}")
    try:
        remove_bad_config(NAMESPACE, RATINGS_DEPLOYMENT)
        log.info("Removed bad config from ratings-v1")
    except Exception as e:
        log.warning(f"Config cleanup failed: {e}")

    # --- Phase 9: Cross-model RCA judge ---
    log.info(f"\n{'='*60}")
    log.info("Phase 9: Cross-model RCA evaluation (distributed scenario)")
    log.info(f"{'='*60}")

    judge_matrix = await run_judge_matrix(results, truth)
    for mk in results:
        results[mk]["judge_scores"] = judge_matrix.get(mk, {})
        results[mk]["score"] = rescore_with_eval(
            results[mk]["score"], results[mk]["judge_scores"]
        )
        rca_eval = results[mk]["score"]["category_scores"]["rca_eval"]
        log.info(f"[{mk}] RCA Eval: {rca_eval:.2f} -> "
                 f"Final: {results[mk]['score']['weighted_score']} "
                 f"({results[mk]['score']['result']})")

        # Log to MLFlow Harness (evaluation tracking)
        log_harness_eval(
            run_id=f"distributed-{mk}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            model_id=MODELS[mk]["model_id"],
            scenario="distributed-cascading-multi-service",
            scores=results[mk]["score"]["category_scores"],
            result=results[mk]["score"]["result"],
            weighted_score=results[mk]["score"]["weighted_score"],
            judge_matrix=results[mk].get("judge_scores", {}),
            mlflow_url=MLFLOW_HARNESS_URL,
            tags={
                "model_key": mk,
                "scenario_type": "distributed",
                "causes_found": str(results[mk]["score"].get("multi_cause", {}).get("causes_found", 0)),
                "total_causes": str(results[mk]["score"].get("multi_cause", {}).get("total_causes", 0)),
            },
        )

    # --- Phase 10: Write results ---
    log.info(f"\n{'='*60}")
    log.info("Phase 10: Writing benchmark results")
    log.info(f"{'='*60}")

    output_dir = Path("artifacts/distributed-benchmark-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
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

    summary = {"benchmark_time": datetime.now(timezone.utc).isoformat(),
               "scenario": "distributed_cascading_failure", "models": {}}
    for mk, data in results.items():
        mc = data["score"].get("multi_cause", {})
        summary["models"][mk] = {
            "name": data["model"],
            "weighted_score": data["score"]["weighted_score"],
            "result": data["score"]["result"],
            "category_scores": data["score"]["category_scores"],
            "multi_cause": mc,
            "elapsed_seconds": data["elapsed_seconds"],
            "tool_calls_count": len(data["aiops_output"].get("tool_calls", [])),
            "rca_ranked": data["aiops_output"].get("rca_ranked", []),
            "judge_scores": data.get("judge_scores", {}),
        }
    with open(output_dir / "comparison.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    log.info(f"\nArtifacts written to: {output_dir}")

    # --- Final comparison (box-drawing tables) ---
    model_keys = [mk for mk in ["granite-4-tiny", "granite-4-tiny-lightspeed",
                                 "qwen3-coder-next", "qwen3-coder-next-lightspeed",
                                 "gemini-3-pro"]
                  if mk in results]

    MODEL_LABELS = {
        "granite-4-tiny": "Granite",
        "granite-4-tiny-lightspeed": "Granite + LS",
        "gemini-3-pro": "Gemini 3 Pro",
        "qwen3-coder-next": "Qwen3",
        "qwen3-coder-next-lightspeed": "Qwen3 + LS",
    }

    table_rows = []
    for mk in model_keys:
        r = results[mk]
        s = r.get("score", {})
        cats = s.get("category_scores", {})
        js = r.get("judge_scores", {})
        mc = s.get("multi_cause", {})
        rca_eval_raw = cats.get("rca_eval", 0)
        rca_eval_10 = rca_eval_raw * 10
        table_rows.append({
            "model": MODEL_LABELS.get(mk, mk),
            "rca_detected": "Pass" if cats.get("rca_detected", 0) >= 0.5 else "Fail",
            "causes": f"{mc.get('causes_found', 0)}/{mc.get('total_causes', 0)}",
            "score": f"{s.get('weighted_score', 0):.2f}",
            "rca_eval": f"{rca_eval_10:.1f}/10",
            "tool_calls": str(len(r.get("aiops_output", {}).get("tool_calls", []))),
            "hallucinated": _hallucination_check(js),
            "result": s.get("result", "?"),
            "time": f"{r.get('elapsed_seconds', 0):.1f}s",
        })

    cols = [
        ("Model",          "model",        16),
        ("RCA Detected",   "rca_detected", 13),
        ("Causes Found",   "causes",        13),
        ("Score",          "score",          7),
        ("RCA Eval",       "rca_eval",     10),
        ("Tool Calls",     "tool_calls",   10),
        ("Hallucinated?",  "hallucinated", 13),
        ("Result",         "result",        7),
        ("Time",           "time",          7),
    ]

    print("\n")
    print("DISTRIBUTED BENCHMARK RESULTS (Staggered Dual-Fault Cascade)")
    print(_box_table(cols, table_rows))

    # RCA Hypotheses
    print("\nRCA Hypotheses")
    for mk in model_keys:
        r = results.get(mk, {})
        print(f"\n  {MODEL_LABELS.get(mk, mk)}:")
        for i, h in enumerate(r.get("aiops_output", {}).get("rca_ranked", [])):
            print(f"    {i+1}. {h}")

    # RCA Eval Matrix
    eval_col_w = 12
    eval_cols = [("Model", "model", 16)]
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

    print("\nCross-Model RCA Eval Matrix (Distributed Scenario)")
    print(_box_table(eval_cols, eval_rows))
    print("  (Each cell = row model's RCA scored by column model, 1-10 scale)")
    print("  (Judges evaluate multi-cause detection: did the agent find BOTH root causes?)")

    print(f"\nFull artifacts: {output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    asyncio.run(run_benchmark())
