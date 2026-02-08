"""Logs integration — queries pod logs via Kubernetes API.

Falls back to direct pod log reading when Loki is not available.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from kubernetes import client, config


def _load_k8s():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def search_logs(
    namespace: str = "bookinfo",
    pod_name: Optional[str] = None,
    container: Optional[str] = None,
    search_text: Optional[str] = None,
    since_minutes: int = 30,
    limit: int = 100,
) -> list[dict]:
    """Search pod logs via the Kubernetes API."""
    _load_k8s()
    v1 = client.CoreV1Api()

    since_seconds = since_minutes * 60
    results = []

    if pod_name:
        pods = [pod_name]
    else:
        pod_list = v1.list_namespaced_pod(namespace=namespace)
        pods = [p.metadata.name for p in pod_list.items if p.status.phase == "Running"]

    for pname in pods:
        try:
            kwargs = {
                "name": pname,
                "namespace": namespace,
                "since_seconds": since_seconds,
                "tail_lines": limit,
            }
            if container:
                kwargs["container"] = container

            log_text = v1.read_namespaced_pod_log(**kwargs)
        except Exception as e:
            results.append({"pod": pname, "error": str(e)})
            continue

        lines = log_text.strip().split("\n") if log_text else []

        if search_text:
            lines = [l for l in lines if search_text.lower() in l.lower()]

        for line in lines[:limit]:
            results.append({"pod": pname, "log": line})

        if len(results) >= limit:
            break

    return results[:limit]
