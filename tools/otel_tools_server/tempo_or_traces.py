"""Traces integration — placeholder for Tempo/Jaeger.

Returns a descriptive message when no tracing backend is configured.
Implement Tempo or Jaeger queries when the backend is available.
"""

from typing import Optional


def get_trace_waterfall(
    trace_id: Optional[str] = None,
    service: Optional[str] = None,
    namespace: str = "bookinfo",
    since_minutes: int = 30,
) -> list[dict]:
    """Retrieve distributed trace data.

    Currently returns a placeholder indicating no tracing backend is configured.
    When Tempo or Jaeger is deployed, this can be connected to their APIs.
    """
    return [{
        "status": "not_configured",
        "message": (
            "Distributed tracing backend (Tempo/Jaeger) is not configured. "
            "Use getMetricHistory and getK8sEvents for evidence gathering. "
            "Trace data will be available when a tracing backend is deployed."
        ),
        "requested_trace_id": trace_id,
        "requested_service": service,
    }]
