"""Evidence collection — gathers telemetry from the tools server and K8s API."""

import logging
import os
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

TOOLS_SERVER_URL = os.environ.get(
    "TOOLS_SERVER_URL",
    "http://aiops-tools-server.aiops-harness.svc:8000",
)


async def collect_evidence(
    namespace: str,
    deployment_name: str,
    start_time: str,
    end_time: str,
    fault_type: str,
) -> dict:
    """Collect evidence from Prometheus and K8s events during the fault window.

    Returns an evidence bundle with metric summaries and event lists.
    """
    evidence = {
        "collection_time": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start_time, "end": end_time},
        "metrics": {},
        "events": [],
        "logs": [],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Collect CPU metrics
        evidence["metrics"]["cpu"] = await _query_metric(
            client,
            f'rate(container_cpu_usage_seconds_total{{namespace="{namespace}", '
            f'pod=~"{deployment_name}.*"}}[5m])',
            start_time, end_time,
        )

        # Collect memory metrics
        evidence["metrics"]["memory"] = await _query_metric(
            client,
            f'container_memory_working_set_bytes{{namespace="{namespace}", '
            f'pod=~"{deployment_name}.*"}}',
            start_time, end_time,
        )

        # Collect restart count
        evidence["metrics"]["restarts"] = await _query_metric(
            client,
            f'kube_pod_container_status_restarts_total{{namespace="{namespace}", '
            f'pod=~"{deployment_name}.*"}}',
            start_time, end_time,
        )

        # Collect pod status
        evidence["metrics"]["pod_status"] = await _query_metric(
            client,
            f'kube_pod_status_phase{{namespace="{namespace}", '
            f'pod=~"{deployment_name}.*"}}',
            start_time, end_time,
        )

        # Collect container waiting reasons (for CrashLoopBackOff)
        if fault_type == "crashloop_bad_config":
            evidence["metrics"]["waiting_reason"] = await _query_metric(
                client,
                f'kube_pod_container_status_waiting_reason{{namespace="{namespace}", '
                f'pod=~"{deployment_name}.*"}}',
                start_time, end_time,
            )

        # Collect K8s events
        evidence["events"] = await _get_events(client, namespace, since_minutes=15)

        # Collect logs (best effort)
        evidence["logs"] = await _get_logs(client, namespace, deployment_name)

    return evidence


async def _query_metric(client: httpx.AsyncClient, query: str, start: str, end: str) -> dict:
    """Query a metric from the tools server."""
    try:
        resp = await client.post(
            f"{TOOLS_SERVER_URL}/tools/getMetricHistory",
            json={"query": query, "start": start, "end": end, "step": "30s"},
        )
        resp.raise_for_status()
        return resp.json().get("result", {})
    except Exception as e:
        log.warning(f"Failed to query metric: {e}")
        return {"error": str(e)}


async def _get_events(client: httpx.AsyncClient, namespace: str, since_minutes: int = 15) -> list:
    """Get K8s events from the tools server."""
    try:
        resp = await client.post(
            f"{TOOLS_SERVER_URL}/tools/getK8sEvents",
            json={"namespace": namespace, "since_minutes": since_minutes},
        )
        resp.raise_for_status()
        return resp.json().get("events", [])
    except Exception as e:
        log.warning(f"Failed to get events: {e}")
        return [{"error": str(e)}]


async def _get_logs(client: httpx.AsyncClient, namespace: str, deployment_name: str) -> list:
    """Get pod logs from the tools server."""
    try:
        resp = await client.post(
            f"{TOOLS_SERVER_URL}/tools/searchLogs",
            json={
                "namespace": namespace,
                "search_text": "error",
                "since_minutes": 15,
                "limit": 50,
            },
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        log.warning(f"Failed to get logs: {e}")
        return [{"error": str(e)}]


def build_evidence_pointers(evidence: dict, fault_type: str) -> list[str]:
    """Build evidence pointer strings for inclusion in aiops_output.json."""
    pointers = []

    cpu_data = evidence.get("metrics", {}).get("cpu", {})
    if cpu_data and cpu_data.get("data"):
        pointers.append("prometheus:container_cpu_usage_seconds_total")

    mem_data = evidence.get("metrics", {}).get("memory", {})
    if mem_data and mem_data.get("data"):
        pointers.append("prometheus:container_memory_working_set_bytes")

    restart_data = evidence.get("metrics", {}).get("restarts", {})
    if restart_data and restart_data.get("data"):
        pointers.append("prometheus:kube_pod_container_status_restarts_total")

    events = evidence.get("events", [])
    if events:
        pointers.append(f"k8s_events:count={len(events)}")

    if fault_type == "crashloop_bad_config":
        waiting = evidence.get("metrics", {}).get("waiting_reason", {})
        if waiting and waiting.get("data"):
            pointers.append("prometheus:kube_pod_container_status_waiting_reason")

    return pointers
