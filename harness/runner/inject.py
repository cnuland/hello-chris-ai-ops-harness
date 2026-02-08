"""Fault injection helpers for CPU saturation and CrashLoopBackOff scenarios."""

import logging
import time
from typing import Optional

from kubernetes import client, config

log = logging.getLogger(__name__)


def _load_k8s():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def inject_cpu_saturation(
    namespace: str,
    deployment_name: str,
    cpu_percent: int = 95,
    duration_seconds: int = 300,
) -> dict:
    """Inject CPU saturation by patching the deployment to add a stress-ng sidecar.

    Adds an init-less sidecar container running stress-ng that consumes CPU.
    Returns injection metadata for truth.json.
    """
    _load_k8s()
    apps_v1 = client.AppsV1Api()

    # Get current deployment
    deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)

    # Check if stress container already exists
    containers = deploy.spec.template.spec.containers
    existing_names = [c.name for c in containers]
    if "stress-injector" in existing_names:
        log.warning("stress-injector container already exists, skipping injection")
        return {"status": "already_injected", "deployment": deployment_name}

    # Add stress-ng sidecar
    cpu_count = 1  # stress 1 CPU core
    stress_container = client.V1Container(
        name="stress-injector",
        image="alexeiled/stress-ng:latest",
        command=["stress-ng"],
        args=[
            "--cpu", str(cpu_count),
            "--cpu-load", str(cpu_percent),
            "--timeout", f"{duration_seconds}s",
            "--metrics-brief",
        ],
        resources=client.V1ResourceRequirements(
            requests={"cpu": "100m", "memory": "32Mi"},
            limits={"cpu": "500m", "memory": "64Mi"},
        ),
    )

    # Patch the deployment to add the stress container
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        *[_container_to_dict(c) for c in containers],
                        _container_to_dict(stress_container),
                    ]
                }
            }
        }
    }

    apps_v1.patch_namespaced_deployment(deployment_name, namespace, patch)
    log.info(f"Injected CPU saturation into {namespace}/{deployment_name} "
             f"({cpu_percent}% for {duration_seconds}s)")

    return {
        "fault_type": "cpu_saturation",
        "target": f"{namespace}/{deployment_name}",
        "parameters": {
            "cpu_percent": cpu_percent,
            "duration_seconds": duration_seconds,
        },
        "status": "injected",
    }


def remove_cpu_saturation(namespace: str, deployment_name: str) -> dict:
    """Remove the stress-ng sidecar from the deployment."""
    _load_k8s()
    apps_v1 = client.AppsV1Api()

    deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    containers = deploy.spec.template.spec.containers
    filtered = [c for c in containers if c.name != "stress-injector"]

    if len(filtered) == len(containers):
        return {"status": "not_found", "deployment": deployment_name}

    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [_container_to_dict(c) for c in filtered]
                }
            }
        }
    }

    apps_v1.patch_namespaced_deployment(deployment_name, namespace, patch)
    log.info(f"Removed CPU saturation from {namespace}/{deployment_name}")
    return {"status": "removed", "deployment": deployment_name}


def inject_crashloop(
    namespace: str,
    deployment_name: str,
    bad_env_var: str = "INVALID_DB_HOST",
    bad_env_value: str = "this-host-does-not-exist.invalid",
) -> dict:
    """Inject CrashLoopBackOff by adding a bad environment variable.

    This causes the container to fail on startup, entering CrashLoopBackOff.
    """
    _load_k8s()
    apps_v1 = client.AppsV1Api()

    deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    container = deploy.spec.template.spec.containers[0]

    # Add the bad env var
    env_list = container.env or []
    existing_names = [e.name for e in env_list]
    if bad_env_var in existing_names:
        return {"status": "already_injected", "deployment": deployment_name}

    # Patch: add bad env var AND set command to fail
    # For bookinfo ratings, setting a bad env var and adding a pre-start
    # check that exits non-zero
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "harness.aiops/injected-fault": "crashloop",
                    }
                },
                "spec": {
                    "containers": [{
                        "name": container.name,
                        "env": [
                            *[{"name": e.name, "value": e.value} for e in env_list if e.value],
                            {"name": bad_env_var, "value": bad_env_value},
                            {"name": "HARNESS_CRASH_INJECT", "value": "true"},
                        ],
                        # Override command to crash immediately
                        "command": ["/bin/sh", "-c"],
                        "args": [
                            "echo 'FATAL: Cannot connect to database at $INVALID_DB_HOST' && exit 1"
                        ],
                    }],
                },
            }
        }
    }

    apps_v1.patch_namespaced_deployment(deployment_name, namespace, patch)
    log.info(f"Injected CrashLoopBackOff into {namespace}/{deployment_name}")

    return {
        "fault_type": "crashloop_bad_config",
        "target": f"{namespace}/{deployment_name}",
        "parameters": {
            "bad_env_var": bad_env_var,
            "bad_env_value": bad_env_value,
        },
        "status": "injected",
    }


def remove_crashloop(namespace: str, deployment_name: str, original_image: Optional[str] = None) -> dict:
    """Remove the CrashLoopBackOff injection by reverting the deployment."""
    _load_k8s()
    apps_v1 = client.AppsV1Api()

    deploy = apps_v1.read_namespaced_deployment(deployment_name, namespace)
    container = deploy.spec.template.spec.containers[0]

    # Remove injected env vars and restore original command
    env_list = container.env or []
    clean_env = [
        {"name": e.name, "value": e.value}
        for e in env_list
        if e.name not in ("INVALID_DB_HOST", "HARNESS_CRASH_INJECT") and e.value
    ]

    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "harness.aiops/injected-fault": None,
                    }
                },
                "spec": {
                    "containers": [{
                        "name": container.name,
                        "env": clean_env if clean_env else None,
                        "command": None,
                        "args": None,
                    }],
                },
            }
        }
    }

    apps_v1.patch_namespaced_deployment(deployment_name, namespace, patch)
    log.info(f"Removed CrashLoopBackOff injection from {namespace}/{deployment_name}")

    return {"status": "removed", "deployment": deployment_name}


def _container_to_dict(c) -> dict:
    """Convert a V1Container to a dict for JSON patch."""
    if isinstance(c, dict):
        return c
    d = {"name": c.name, "image": c.image}
    if c.command:
        d["command"] = c.command
    if c.args:
        d["args"] = c.args
    if c.ports:
        d["ports"] = [{"containerPort": p.container_port, "protocol": p.protocol or "TCP"} for p in c.ports]
    if c.env:
        d["env"] = [{"name": e.name, "value": e.value} for e in c.env if e.value]
    if c.resources:
        res = {}
        if c.resources.requests:
            res["requests"] = c.resources.requests
        if c.resources.limits:
            res["limits"] = c.resources.limits
        if res:
            d["resources"] = res
    if c.image_pull_policy:
        d["imagePullPolicy"] = c.image_pull_policy
    if c.volume_mounts:
        d["volumeMounts"] = [
            {"name": vm.name, "mountPath": vm.mount_path, "subPath": vm.sub_path}
            for vm in c.volume_mounts
        ]
    return d
