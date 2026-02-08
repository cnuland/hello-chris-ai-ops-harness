"""PromQL helpers — query Prometheus/Thanos for metric data."""

import os
import httpx

THANOS_URL = os.environ.get(
    "THANOS_QUERIER_URL",
    "https://thanos-querier.openshift-monitoring.svc:9091",
)
# When running in-cluster, use the SA token for auth
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"


def _get_headers() -> dict:
    """Build auth headers using the in-cluster service account token."""
    try:
        with open(TOKEN_PATH) as f:
            token = f.read().strip()
        return {"Authorization": f"Bearer {token}"}
    except FileNotFoundError:
        return {}


def _get_verify():
    """Return CA bundle path if available, else disable TLS verify for dev."""
    if os.path.exists(CA_PATH):
        return CA_PATH
    # Also check OpenShift injected CA
    openshift_ca = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    if os.path.exists(openshift_ca):
        return openshift_ca
    return False


async def query_prometheus(query: str) -> dict:
    """Execute an instant PromQL query."""
    async with httpx.AsyncClient(verify=_get_verify(), timeout=30.0) as client:
        resp = await client.get(
            f"{THANOS_URL}/api/v1/query",
            params={"query": query},
            headers=_get_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
    return _summarize(data)


async def query_prometheus_range(query: str, start: str, end: str, step: str = "60s") -> dict:
    """Execute a range PromQL query."""
    async with httpx.AsyncClient(verify=_get_verify(), timeout=30.0) as client:
        resp = await client.get(
            f"{THANOS_URL}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
            headers=_get_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
    return _summarize(data)


def _summarize(prom_response: dict) -> dict:
    """Convert raw Prometheus JSON into a compact summary for the agent."""
    status = prom_response.get("status", "unknown")
    result_type = prom_response.get("data", {}).get("resultType", "unknown")
    results = prom_response.get("data", {}).get("result", [])

    summarized = []
    for r in results[:20]:  # cap to avoid context overflow
        metric = r.get("metric", {})
        if result_type == "matrix":
            values = r.get("values", [])
            if values:
                nums = [float(v[1]) for v in values if v[1] != "NaN"]
                summary = {
                    "metric": metric,
                    "samples": len(values),
                    "min": round(min(nums), 4) if nums else None,
                    "max": round(max(nums), 4) if nums else None,
                    "avg": round(sum(nums) / len(nums), 4) if nums else None,
                    "latest": values[-1][1] if values else None,
                }
            else:
                summary = {"metric": metric, "samples": 0}
        elif result_type == "vector":
            value = r.get("value", [None, None])
            summary = {"metric": metric, "value": value[1] if len(value) > 1 else None}
        else:
            summary = {"metric": metric, "raw": r}
        summarized.append(summary)

    return {
        "status": status,
        "resultType": result_type,
        "resultCount": len(results),
        "data": summarized,
    }
