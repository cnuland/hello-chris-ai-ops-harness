"""AIOps Tools Server — FastAPI service exposing investigative tools.

Provides four endpoints that the Llama Stack agent uses for tool-mediated
evidence retrieval during incident investigation:
  - /tools/getMetricHistory   (Prometheus / Thanos)
  - /tools/getK8sEvents       (Kubernetes API)
  - /tools/searchLogs          (placeholder)
  - /tools/getTraceWaterfall   (placeholder)
"""

import os

from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Optional

from .promql import query_prometheus, query_prometheus_range
from .k8s_events import get_k8s_events
from .loki_or_logs import search_logs
from .tempo_or_traces import get_trace_waterfall

app = FastAPI(
    title="AIOps Tools Server",
    description="Tool-mediated evidence retrieval for AIOps harness",
    version="1.0.0",
)


# ---------- Request / Response Models ----------

class MetricHistoryRequest(BaseModel):
    query: str = Field(..., description="PromQL query string")
    start: Optional[str] = Field(None, description="RFC-3339 start time")
    end: Optional[str] = Field(None, description="RFC-3339 end time")
    step: Optional[str] = Field("60s", description="Query resolution step")
    namespace: Optional[str] = Field("bookinfo", description="Target namespace for context")


class K8sEventsRequest(BaseModel):
    namespace: str = Field("bookinfo", description="Kubernetes namespace")
    resource_type: Optional[str] = Field(None, description="Filter by involved object kind")
    resource_name: Optional[str] = Field(None, description="Filter by involved object name")
    since_minutes: Optional[int] = Field(30, description="Look back N minutes")


class SearchLogsRequest(BaseModel):
    namespace: str = Field("bookinfo", description="Kubernetes namespace")
    pod_name: Optional[str] = Field(None, description="Specific pod name")
    container: Optional[str] = Field(None, description="Container name")
    search_text: Optional[str] = Field(None, description="Text pattern to search for")
    since_minutes: Optional[int] = Field(30, description="Look back N minutes")
    limit: Optional[int] = Field(100, description="Max log lines to return")


class TraceWaterfallRequest(BaseModel):
    trace_id: Optional[str] = Field(None, description="Specific trace ID")
    service: Optional[str] = Field(None, description="Service name")
    namespace: str = Field("bookinfo", description="Kubernetes namespace")
    since_minutes: Optional[int] = Field(30, description="Look back N minutes")


# ---------- Endpoints ----------

@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.post("/tools/getMetricHistory")
async def get_metric_history(req: MetricHistoryRequest):
    """Query Prometheus/Thanos for metric history."""
    if req.start and req.end:
        result = await query_prometheus_range(
            query=req.query,
            start=req.start,
            end=req.end,
            step=req.step or "60s",
        )
    else:
        result = await query_prometheus(query=req.query)
    return {"tool": "getMetricHistory", "query": req.query, "result": result}


@app.post("/tools/getK8sEvents")
async def get_k8s_events_endpoint(req: K8sEventsRequest):
    """Retrieve Kubernetes events filtered by namespace and resource."""
    events = get_k8s_events(
        namespace=req.namespace,
        resource_type=req.resource_type,
        resource_name=req.resource_name,
        since_minutes=req.since_minutes or 30,
    )
    return {"tool": "getK8sEvents", "namespace": req.namespace, "events": events}


@app.post("/tools/searchLogs")
async def search_logs_endpoint(req: SearchLogsRequest):
    """Search pod logs for patterns."""
    logs = search_logs(
        namespace=req.namespace,
        pod_name=req.pod_name,
        container=req.container,
        search_text=req.search_text,
        since_minutes=req.since_minutes or 30,
        limit=req.limit or 100,
    )
    return {"tool": "searchLogs", "namespace": req.namespace, "results": logs}


@app.post("/tools/getTraceWaterfall")
async def get_trace_waterfall_endpoint(req: TraceWaterfallRequest):
    """Retrieve distributed trace waterfall data."""
    traces = get_trace_waterfall(
        trace_id=req.trace_id,
        service=req.service,
        namespace=req.namespace,
        since_minutes=req.since_minutes or 30,
    )
    return {"tool": "getTraceWaterfall", "namespace": req.namespace, "traces": traces}
