"""Kubernetes events query — retrieves events from the K8s API."""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from kubernetes import client, config


def _load_k8s():
    """Load in-cluster or local kubeconfig."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def get_k8s_events(
    namespace: str = "bookinfo",
    resource_type: Optional[str] = None,
    resource_name: Optional[str] = None,
    since_minutes: int = 30,
) -> list[dict]:
    """Retrieve Kubernetes events, optionally filtered by resource."""
    _load_k8s()
    v1 = client.CoreV1Api()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    field_selectors = []
    if resource_type:
        field_selectors.append(f"involvedObject.kind={resource_type}")
    if resource_name:
        field_selectors.append(f"involvedObject.name={resource_name}")

    field_selector = ",".join(field_selectors) if field_selectors else None

    kwargs = {"namespace": namespace}
    if field_selector:
        kwargs["field_selector"] = field_selector

    events_list = v1.list_namespaced_event(**kwargs)

    results = []
    for ev in events_list.items:
        event_time = ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp
        if event_time and event_time.replace(tzinfo=timezone.utc) < cutoff:
            continue

        results.append({
            "type": ev.type,
            "reason": ev.reason,
            "message": ev.message,
            "count": ev.count,
            "involved_object": {
                "kind": ev.involved_object.kind,
                "name": ev.involved_object.name,
                "namespace": ev.involved_object.namespace,
            },
            "first_timestamp": ev.first_timestamp.isoformat() if ev.first_timestamp else None,
            "last_timestamp": event_time.isoformat() if event_time else None,
            "source": ev.source.component if ev.source else None,
        })

    # Sort by timestamp descending
    results.sort(key=lambda e: e.get("last_timestamp") or "", reverse=True)
    return results[:50]  # cap to avoid context overflow
