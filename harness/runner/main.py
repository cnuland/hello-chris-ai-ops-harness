"""Harness runner — orchestrates the inject → capture → invoke → score → produce lifecycle.

Reads a HarnessManifest, executes the fault scenario, invokes the Llama Stack agent
through the tools server, and produces the four contract artifacts.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

from .inject import (
    inject_cpu_saturation,
    inject_crashloop,
    remove_cpu_saturation,
    remove_crashloop,
)
from .evidence import collect_evidence, build_evidence_pointers
from .score import score_run
from .storage import write_all_artifacts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("harness-runner")

# Configuration from environment
MANIFEST_PATH = os.environ.get("HARNESS_MANIFEST", "/config/manifest.yaml")
LLAMA_STACK_URL = os.environ.get(
    "LLAMA_STACK_URL",
    "http://granite-4-server.llm-serving.svc.cluster.local:8080",
)
TOOLS_SERVER_URL = os.environ.get(
    "TOOLS_SERVER_URL",
    "http://aiops-tools-server.aiops-harness.svc:8000",
)
MODEL_ID = os.environ.get("LLAMA_MODEL_ID", "granite-4")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "300"))
BASELINE_WAIT = int(os.environ.get("BASELINE_WAIT_SECONDS", "60"))
INJECTION_WAIT = int(os.environ.get("INJECTION_WAIT_SECONDS", "120"))


def generate_run_id() -> str:
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def load_manifest(path: str) -> dict:
    """Load and parse the HarnessManifest YAML."""
    with open(path) as f:
        manifest = yaml.safe_load(f)
    log.info(f"Loaded manifest: {manifest['metadata']['name']}")
    return manifest


async def invoke_agent(
    incident_description: str,
    tools_url: str,
    evidence: dict,
) -> dict:
    """Invoke the Llama Stack agent to investigate the incident.

    Uses the Llama Stack /v1/inference/chat-completion endpoint with tool
    definitions that point to the tools server.
    """
    tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": "getMetricHistory",
                "description": "Query Prometheus for metric history. Use PromQL queries to examine CPU, memory, latency, error rates, and other metrics for services in the bookinfo namespace.",
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
                "description": "Retrieve Kubernetes events filtered by namespace and optionally by resource type/name. Events show pod scheduling, crashes, restarts, image pull errors, and other cluster-level signals.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string", "description": "Kubernetes namespace", "default": "bookinfo"},
                        "resource_type": {"type": "string", "description": "Filter by kind (Pod, Deployment, etc.)"},
                        "resource_name": {"type": "string", "description": "Filter by resource name"},
                        "since_minutes": {"type": "integer", "description": "Look back N minutes", "default": 30},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "searchLogs",
                "description": "Search pod logs for error patterns, exceptions, or specific text within a time window.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string", "default": "bookinfo"},
                        "pod_name": {"type": "string", "description": "Specific pod name"},
                        "search_text": {"type": "string", "description": "Text pattern to search for"},
                        "since_minutes": {"type": "integer", "default": 30},
                    },
                },
            },
        },
    ]

    evidence_summary = _build_evidence_summary(evidence)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert SRE AI agent investigating an operational incident "
                "in a Kubernetes-based microservices application called Bookinfo. "
                "The application has these services: productpage (frontend), details, "
                "reviews (v1, v2, v3), and ratings. "
                "Use the available tools to investigate the incident systematically. "
                "Query metrics, check Kubernetes events, and search logs to identify "
                "the root cause. Provide your findings in a structured format."
            ),
        },
        {
            "role": "user",
            "content": (
                f"INCIDENT REPORT:\n{incident_description}\n\n"
                f"INITIAL EVIDENCE:\n{evidence_summary}\n\n"
                "Please investigate this incident using the available tools. "
                "After your investigation, provide:\n"
                "1. An incident summary\n"
                "2. Ranked root cause hypotheses (most likely first)\n"
                "3. A recommended remediation action\n"
                "4. List of evidence that supports your conclusion\n\n"
                "Format your final answer as JSON with keys: "
                "incident_summary, rca_ranked (list of strings), "
                "recommended_action, evidence_links (list of strings)"
            ),
        },
    ]

    tool_calls_log = []

    async with httpx.AsyncClient(timeout=float(AGENT_TIMEOUT)) as client:
        # Use OpenAI-compatible chat completion endpoint
        try:
            resp = await client.post(
                f"{LLAMA_STACK_URL}/v1/chat/completions",
                json={
                    "model": MODEL_ID,
                    "messages": messages,
                    "tools": tool_definitions,
                    "tool_choice": "auto",
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            log.error(f"Agent invocation failed: {e}")
            return _fallback_output(str(e), evidence, tool_calls_log)

        # Process response — handle tool calls if any
        choices = result.get("choices", [])
        if not choices:
            return _fallback_output("No choices in response", evidence, tool_calls_log)

        message = choices[0].get("message", {})

        # Check for tool calls
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_args = {}

                log.info(f"Agent tool call: {tool_name}({tool_args})")

                # Execute tool call against tools server
                tool_result = await _execute_tool_call(client, tool_name, tool_args)
                tool_calls_log.append({
                    "tool": tool_name,
                    "arguments": tool_args,
                    "result_summary": _truncate(str(tool_result), 500),
                })

                # Add tool results to conversation
                messages.append(message)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(tool_result, default=str)[:2000],
                })

            # Get final response with tool results
            try:
                resp2 = await client.post(
                    f"{LLAMA_STACK_URL}/v1/chat/completions",
                    json={
                        "model": MODEL_ID,
                        "messages": messages,
                        "max_tokens": 4096,
                    },
                )
                resp2.raise_for_status()
                result2 = resp2.json()
                choices = result2.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
            except Exception as e:
                log.warning(f"Follow-up call failed: {e}")

        # Parse the agent's response
        content = message.get("content", "")
        return _parse_agent_response(content, evidence, tool_calls_log)


async def _execute_tool_call(client: httpx.AsyncClient, tool_name: str, args: dict) -> dict:
    """Execute a tool call against the tools server."""
    endpoint_map = {
        "getMetricHistory": "/tools/getMetricHistory",
        "getK8sEvents": "/tools/getK8sEvents",
        "searchLogs": "/tools/searchLogs",
        "getTraceWaterfall": "/tools/getTraceWaterfall",
    }

    endpoint = endpoint_map.get(tool_name)
    if not endpoint:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        resp = await client.post(f"{TOOLS_SERVER_URL}{endpoint}", json=args)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def _parse_agent_response(content: str, evidence: dict, tool_calls: list) -> dict:
    """Parse the agent's text response into structured output."""
    # Try to extract JSON from the response
    try:
        # Look for JSON block in the response
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(content[json_start:json_end])
            parsed["tool_calls"] = tool_calls
            if "evidence_links" not in parsed:
                parsed["evidence_links"] = build_evidence_pointers(
                    evidence, evidence.get("fault_type", "")
                )
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: construct output from text
    return {
        "incident_summary": content[:500] if content else "Agent did not produce a summary",
        "rca_ranked": _extract_rca_from_text(content),
        "recommended_action": _extract_action_from_text(content),
        "evidence_links": build_evidence_pointers(evidence, evidence.get("fault_type", "")),
        "tool_calls": tool_calls,
        "raw_response": content[:2000],
    }


def _fallback_output(error: str, evidence: dict, tool_calls: list) -> dict:
    """Produce a fallback output when the agent fails."""
    return {
        "incident_summary": f"Agent invocation error: {error}",
        "rca_ranked": [],
        "recommended_action": "Manual investigation required",
        "evidence_links": build_evidence_pointers(evidence, evidence.get("fault_type", "")),
        "tool_calls": tool_calls,
        "error": error,
    }


def _extract_rca_from_text(text: str) -> list[str]:
    """Best-effort extraction of root cause hypotheses from text."""
    candidates = []
    text_lower = text.lower()
    if "cpu" in text_lower and ("saturation" in text_lower or "high" in text_lower):
        candidates.append("cpu_saturation")
    if "crashloop" in text_lower or "crash" in text_lower:
        candidates.append("crashloop_bad_config")
    if "reviews" in text_lower:
        for v in ["reviews-v2", "reviews-v1", "reviews-v3"]:
            if v in text_lower:
                candidates.append(f"bookinfo/{v}:{candidates[0] if candidates else 'unknown'}")
    if "ratings" in text_lower:
        candidates.append(f"bookinfo/ratings-v1:{candidates[0] if candidates else 'unknown'}")
    return candidates if candidates else ["unknown"]


def _extract_action_from_text(text: str) -> str:
    """Best-effort extraction of recommended action from text."""
    text_lower = text.lower()
    if "scale" in text_lower:
        return "Scale the affected deployment"
    if "restart" in text_lower:
        return "Restart the affected pods"
    if "rollback" in text_lower:
        return "Rollback the deployment"
    if "config" in text_lower or "env" in text_lower:
        return "Fix the configuration/environment variable"
    return "Investigate and remediate manually"


def _build_evidence_summary(evidence: dict) -> str:
    """Build a text summary of collected evidence for the agent."""
    lines = []
    metrics = evidence.get("metrics", {})
    for name, data in metrics.items():
        if isinstance(data, dict) and data.get("data"):
            for item in data["data"][:3]:
                metric_labels = item.get("metric", {})
                if "value" in item:
                    lines.append(f"  {name}: {metric_labels} = {item['value']}")
                elif "avg" in item:
                    lines.append(f"  {name}: {metric_labels} avg={item['avg']} max={item['max']}")

    events = evidence.get("events", [])
    if events:
        lines.append(f"\nKubernetes Events ({len(events)} recent):")
        for ev in events[:5]:
            lines.append(f"  [{ev.get('type')}] {ev.get('reason')}: {ev.get('message', '')[:100]}")

    return "\n".join(lines) if lines else "No evidence collected yet"


def _truncate(s: str, max_len: int) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s


async def run_harness(manifest: dict) -> dict:
    """Execute the full harness lifecycle."""
    run_id = generate_run_id()
    spec = manifest.get("spec", {})
    scenario = spec.get("scenario", {})
    fault = scenario.get("fault", {})
    sut = spec.get("sut", {})

    namespace = sut.get("workload", {}).get("namespace", "bookinfo")
    target = fault.get("targetSelector", {})
    deployment_name = target.get("name", "")
    fault_type = fault.get("type", "")
    params = fault.get("parameters", {})

    log.info(f"=== Harness Run: {run_id} ===")
    log.info(f"Scenario: {scenario.get('id')}")
    log.info(f"Fault: {fault_type} -> {namespace}/{deployment_name}")

    # Build run.json
    run_meta = {
        "run_id": run_id,
        "sut": {
            "type": sut.get("type", "ocp"),
            "cluster_version": sut.get("clusterVersion", "unknown"),
            "namespace": namespace,
        },
        "scenario": scenario.get("id"),
        "manifest": manifest.get("metadata", {}).get("name"),
        "timestamps": {},
        "status": "running",
    }

    # Build truth.json
    if fault_type == "cpu_saturation":
        truth = {
            "root_cause": {
                "label": f"bookinfo/{deployment_name}:cpu_saturation",
                "confidence": 1.0,
            },
            "fault": {
                "type": fault_type,
                "target": f"{namespace}/{deployment_name}",
                "parameters": params,
            },
        }
    elif fault_type == "crashloop_bad_config":
        truth = {
            "root_cause": {
                "label": f"bookinfo/{deployment_name}:crashloop_bad_config",
                "confidence": 1.0,
            },
            "fault": {
                "type": fault_type,
                "target": f"{namespace}/{deployment_name}",
                "parameters": params,
            },
        }
    else:
        truth = {
            "root_cause": {
                "label": f"bookinfo/{deployment_name}:{fault_type}",
                "confidence": 1.0,
            },
        }

    # Phase 1: Baseline
    log.info(f"Phase 1: Baseline ({BASELINE_WAIT}s)")
    run_meta["timestamps"]["baseline_start"] = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(BASELINE_WAIT)
    run_meta["timestamps"]["baseline_end"] = datetime.now(timezone.utc).isoformat()

    # Phase 2: Inject
    log.info("Phase 2: Inject fault")
    run_meta["timestamps"]["inject_start"] = datetime.now(timezone.utc).isoformat()
    injection_start = datetime.now(timezone.utc)

    if fault_type == "cpu_saturation":
        injection_result = inject_cpu_saturation(
            namespace=namespace,
            deployment_name=deployment_name,
            cpu_percent=params.get("cpuPercent", 95),
            duration_seconds=params.get("durationSeconds", 300),
        )
    elif fault_type == "crashloop_bad_config":
        injection_result = inject_crashloop(
            namespace=namespace,
            deployment_name=deployment_name,
            bad_env_var=params.get("envVar", "INVALID_DB_HOST"),
            bad_env_value=params.get("envValue", "this-host-does-not-exist.invalid"),
        )
    else:
        log.error(f"Unknown fault type: {fault_type}")
        run_meta["status"] = "error"
        return run_meta

    log.info(f"Injection result: {injection_result}")
    run_meta["timestamps"]["inject_end"] = datetime.now(timezone.utc).isoformat()

    # Phase 3: Wait for fault to propagate
    log.info(f"Phase 3: Waiting for fault propagation ({INJECTION_WAIT}s)")
    await asyncio.sleep(INJECTION_WAIT)

    # Phase 4: Capture evidence
    log.info("Phase 4: Capture evidence")
    run_meta["timestamps"]["capture_start"] = datetime.now(timezone.utc).isoformat()
    evidence_end = datetime.now(timezone.utc)
    evidence_start = injection_start - timedelta(minutes=2)

    evidence = await collect_evidence(
        namespace=namespace,
        deployment_name=deployment_name,
        start_time=evidence_start.isoformat(),
        end_time=evidence_end.isoformat(),
        fault_type=fault_type,
    )
    run_meta["timestamps"]["capture_end"] = datetime.now(timezone.utc).isoformat()

    # Phase 5: Invoke agent
    log.info("Phase 5: Invoke Llama Stack agent")
    run_meta["timestamps"]["invoke_start"] = datetime.now(timezone.utc).isoformat()

    incident_description = _build_incident_description(scenario, fault, namespace, deployment_name)
    aiops_output = await invoke_agent(incident_description, TOOLS_SERVER_URL, evidence)
    run_meta["timestamps"]["invoke_end"] = datetime.now(timezone.utc).isoformat()

    # Phase 6: Score
    log.info("Phase 6: Score agent output")
    run_meta["timestamps"]["score_start"] = datetime.now(timezone.utc).isoformat()
    score_result = score_run(truth, aiops_output)
    run_meta["timestamps"]["score_end"] = datetime.now(timezone.utc).isoformat()

    # Phase 7: Cleanup (remove injection)
    log.info("Phase 7: Cleanup injection")
    if fault_type == "cpu_saturation":
        remove_cpu_saturation(namespace, deployment_name)
    elif fault_type == "crashloop_bad_config":
        remove_crashloop(namespace, deployment_name)

    # Finalize
    run_meta["status"] = "completed"
    run_meta["timestamps"]["completed"] = datetime.now(timezone.utc).isoformat()

    # Write artifacts
    log.info("Writing artifacts...")
    paths = write_all_artifacts(run_id, run_meta, truth, aiops_output, score_result)
    for name, path in paths.items():
        log.info(f"  {name}: {path}")

    # Print summary
    log.info("=" * 60)
    log.info(f"Run ID:    {run_id}")
    log.info(f"Scenario:  {scenario.get('id')}")
    log.info(f"Score:     {score_result['weighted_score']}")
    log.info(f"Result:    {score_result['result']}")
    log.info(f"RCA:       {score_result['category_scores']['rca']}")
    log.info("=" * 60)

    return run_meta


def _build_incident_description(scenario: dict, fault: dict, namespace: str, deployment: str) -> str:
    """Build the incident description presented to the agent."""
    return (
        f"An operational incident has been detected in the '{namespace}' namespace. "
        f"There are reports of service degradation affecting the Bookinfo application. "
        f"Users may be experiencing increased latency or errors when accessing the product page. "
        f"Please investigate using the available tools to determine the root cause "
        f"and recommend an appropriate remediation action."
    )


def main():
    """Entry point."""
    manifest_path = MANIFEST_PATH

    # Allow overriding via CLI arg
    if len(sys.argv) > 1:
        manifest_path = sys.argv[1]

    log.info(f"Loading manifest from: {manifest_path}")
    manifest = load_manifest(manifest_path)

    result = asyncio.run(run_harness(manifest))
    log.info(f"Harness run completed with status: {result.get('status')}")

    if result.get("status") != "completed":
        sys.exit(1)


if __name__ == "__main__":
    main()
