"""MLFlow experiment tracking utilities for the AIOps Harness.

MLFlow is the opinionated experiment tracking backbone of the AI Harness.
Every benchmark run logs to two MLFlow instances:

  1. AIOps Pipeline Tracker (mlflow-aiops):
     Tracks how the AIOps pipeline investigates incidents.
     Each Llama Stack agent session = one MLFlow run.
     Metrics: tool calls, investigation time, MTTD, model used.

  2. Harness Evaluation Tracker (mlflow-harness):
     Tracks how well the pipeline investigated, based on harness scoring.
     Each harness evaluation = one MLFlow run.
     Metrics: 6 scoring dimensions, weighted score, PASS/FAIL, eval model assessment.

This separation maintains the External Independence Principle:
the pipeline team sees investigation metrics, the evaluation team
sees scoring metrics, and neither can influence the other.

Usage:
    from mlflow_utils import log_aiops_run, log_harness_eval, setup_mlflow

    # After AIOps pipeline completes investigation
    log_aiops_run(
        model_id="granite-4",
        scenario="cpu-saturation-reviews",
        tool_calls=[...],
        rca_output={...},
        investigation_time_seconds=12.5,
    )

    # After harness completes evaluation
    log_harness_eval(
        run_id="run-20260223T174643Z",
        model_id="granite-4",
        scenario="cpu-saturation-reviews",
        scores={"detection": 0.9, "correlation": 0.8, ...},
        judge_matrix={...},
        result="PASS",
        weighted_score=0.87,
    )
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("mlflow-utils")

# ---------------------------------------------------------------------------
# MLFlow import with graceful fallback
# ---------------------------------------------------------------------------

try:
    import mlflow
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False
    log.warning(
        "mlflow package not installed. Install with: pip install mlflow>=2.18.0. "
        "Benchmark runs will continue but results will NOT be tracked in MLFlow."
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default MLFlow tracking URIs (in-cluster service DNS)
_MLFLOW_AIOPS_SVC = "http://mlflow-aiops.mlflow-aiops.svc:5000"
_MLFLOW_HARNESS_SVC = "http://mlflow-harness.mlflow-harness.svc:5000"

# Experiment names
AIOPS_EXPERIMENT = "aiops-rca-pipeline"
HARNESS_EXPERIMENT = "aiops-harness-evaluation"


def _discover_mlflow_route(namespace: str, service_name: str) -> str:
    """Auto-discover MLFlow route from the OpenShift cluster."""
    try:
        result = subprocess.run(
            ["oc", "get", "route", service_name, "-n", namespace,
             "-o", "jsonpath={.spec.host}"],
            capture_output=True, text=True, timeout=10,
        )
        host = result.stdout.strip()
        if host:
            return f"https://{host}"
    except Exception:
        pass
    return ""


def get_mlflow_aiops_url() -> str:
    """Get the MLFlow AIOps tracking URL, auto-discovering from cluster if needed."""
    url = os.environ.get("MLFLOW_AIOPS_URL", "")
    if url:
        return url
    # Try route discovery (for local runs against cluster)
    route_url = _discover_mlflow_route("mlflow-aiops", "mlflow-aiops")
    if route_url:
        return route_url
    # Fall back to in-cluster service DNS
    return _MLFLOW_AIOPS_SVC


def get_mlflow_harness_url() -> str:
    """Get the MLFlow Harness tracking URL, auto-discovering from cluster if needed."""
    url = os.environ.get("MLFLOW_HARNESS_URL", "")
    if url:
        return url
    route_url = _discover_mlflow_route("mlflow-harness", "mlflow-harness")
    if route_url:
        return route_url
    return _MLFLOW_HARNESS_SVC


def setup_mlflow(tracking_uri: str, experiment_name: str) -> bool:
    """Configure MLFlow tracking URI and experiment.

    Returns True if MLFlow is available and configured, False otherwise.
    """
    if not MLFLOW_AVAILABLE:
        log.warning("MLFlow not available — skipping setup")
        return False
    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)
        log.info(f"MLFlow configured: uri={tracking_uri}, experiment={experiment_name}")
        return True
    except Exception as e:
        log.warning(f"MLFlow setup failed (will continue without tracking): {e}")
        return False


# ---------------------------------------------------------------------------
# AIOps Pipeline Logging
# ---------------------------------------------------------------------------

def log_aiops_run(
    model_id: str,
    scenario: str,
    tool_calls: list[dict[str, Any]],
    rca_output: dict[str, Any] | None = None,
    investigation_time_seconds: float | None = None,
    mttd_seconds: float | None = None,
    mlflow_url: str | None = None,
    tags: dict[str, str] | None = None,
) -> str | None:
    """Log an AIOps pipeline investigation run to the AIOps MLFlow instance.

    Tracks the pipeline's behavior: what model was used, what tools were
    called, how long the investigation took, and what the pipeline concluded.

    Returns the MLFlow run ID, or None if logging failed/unavailable.
    """
    if not MLFLOW_AVAILABLE:
        log.info(f"[mlflow-stub] log_aiops_run: model={model_id}, scenario={scenario}, "
                 f"tools={len(tool_calls)}")
        return None

    url = mlflow_url or get_mlflow_aiops_url()
    try:
        mlflow.set_tracking_uri(url)
        mlflow.set_experiment(AIOPS_EXPERIMENT)

        with mlflow.start_run() as run:
            # Parameters (what was configured)
            mlflow.log_param("model_id", model_id)
            mlflow.log_param("scenario", scenario)
            mlflow.log_param("tool_count", len(tool_calls))
            mlflow.log_param("timestamp", datetime.now(timezone.utc).isoformat())

            # Tags
            if tags:
                mlflow.set_tags(tags)

            # Metrics (what happened)
            if investigation_time_seconds is not None:
                mlflow.log_metric("investigation_time_seconds", investigation_time_seconds)
            if mttd_seconds is not None:
                mlflow.log_metric("mttd_seconds", mttd_seconds)
            mlflow.log_metric("tool_calls_total", len(tool_calls))

            # Per-tool type counts
            tool_type_counts: dict[str, int] = {}
            for tc in tool_calls:
                t = tc.get("tool", "unknown")
                tool_type_counts[t] = tool_type_counts.get(t, 0) + 1
            for tool_type, count in tool_type_counts.items():
                mlflow.log_metric(f"tool_{tool_type}_count", count)

            # Distinct tool types used
            mlflow.log_metric("tool_types_used", len(tool_type_counts))

            # RCA output
            if rca_output:
                rca_ranked = rca_output.get("rca_ranked", [])
                mlflow.log_param("top_hypothesis", rca_ranked[0] if rca_ranked else "none")
                mlflow.log_metric("hypothesis_count", len(rca_ranked))
                mlflow.log_text(
                    json.dumps(rca_output, indent=2, default=str),
                    "aiops_output/rca_output.json",
                )

            # Tool calls log
            if tool_calls:
                mlflow.log_text(
                    json.dumps(tool_calls, indent=2, default=str),
                    "tool_calls/tool_calls.json",
                )

            run_id = run.info.run_id
            log.info(f"[mlflow] Logged AIOps run: {run_id} (model={model_id}, "
                     f"scenario={scenario}, tools={len(tool_calls)})")
            return run_id

    except Exception as e:
        log.warning(f"[mlflow] Failed to log AIOps run (continuing without tracking): {e}")
        return None


# ---------------------------------------------------------------------------
# Harness Evaluation Logging
# ---------------------------------------------------------------------------

def log_harness_eval(
    run_id: str,
    model_id: str,
    scenario: str,
    scores: dict[str, float],
    result: str = "UNKNOWN",
    weighted_score: float | None = None,
    judge_matrix: dict[str, dict[str, Any]] | None = None,
    fact_check_results: dict[str, Any] | None = None,
    mlflow_url: str | None = None,
    tags: dict[str, str] | None = None,
) -> str | None:
    """Log a harness evaluation to the Harness MLFlow instance.

    Tracks evaluation results: how well the pipeline investigated,
    whether its claims held up under fact-checking, and the overall
    PASS/FAIL determination.

    Returns the MLFlow run ID, or None if logging failed/unavailable.
    """
    if not MLFLOW_AVAILABLE:
        log.info(f"[mlflow-stub] log_harness_eval: run={run_id}, model={model_id}, "
                 f"result={result}, weighted={weighted_score}")
        return None

    url = mlflow_url or get_mlflow_harness_url()
    try:
        mlflow.set_tracking_uri(url)
        mlflow.set_experiment(HARNESS_EXPERIMENT)

        with mlflow.start_run() as mlf_run:
            # Parameters
            mlflow.log_param("harness_run_id", run_id)
            mlflow.log_param("model_id", model_id)
            mlflow.log_param("scenario", scenario)
            mlflow.log_param("result", result)
            mlflow.log_param("timestamp", datetime.now(timezone.utc).isoformat())

            # Tags
            if tags:
                mlflow.set_tags(tags)

            # Scoring metrics (all 6 dimensions)
            for dimension, score in scores.items():
                mlflow.log_metric(f"score_{dimension}", score)
            if weighted_score is not None:
                mlflow.log_metric("weighted_score", weighted_score)

            # Pass/fail as numeric metric (for charting)
            mlflow.log_metric("passed", 1.0 if result == "PASS" else 0.0)

            # Judge matrix
            if judge_matrix:
                # Extract aggregate judge scores
                peer_overalls = []
                for judge_key, js in judge_matrix.items():
                    if isinstance(js.get("overall"), (int, float)):
                        peer_overalls.append(js["overall"])
                        mlflow.log_metric(f"judge_{judge_key}_overall", js["overall"])
                if peer_overalls:
                    mlflow.log_metric("judge_avg_overall",
                                      sum(peer_overalls) / len(peer_overalls))
                mlflow.log_text(
                    json.dumps(judge_matrix, indent=2, default=str),
                    "judge_matrix/judge_matrix.json",
                )

            # Fact-check results
            if fact_check_results:
                mlflow.log_text(
                    json.dumps(fact_check_results, indent=2, default=str),
                    "fact_check/fact_check.json",
                )

            mlf_run_id = mlf_run.info.run_id
            log.info(f"[mlflow] Logged harness eval: {mlf_run_id} (model={model_id}, "
                     f"result={result}, score={weighted_score})")
            return mlf_run_id

    except Exception as e:
        log.warning(f"[mlflow] Failed to log harness eval (continuing without tracking): {e}")
        return None


# ---------------------------------------------------------------------------
# Distributed Scenario Logging
# ---------------------------------------------------------------------------

def log_distributed_run(
    model_id: str,
    scenario: str,
    tool_calls: list[dict[str, Any]],
    rca_output: dict[str, Any] | None = None,
    investigation_time_seconds: float | None = None,
    causes_found: int = 0,
    total_causes: int = 0,
    rca_completeness: float = 0.0,
    fault1_time: str | None = None,
    fault2_time: str | None = None,
    stagger_seconds: int = 60,
    mlflow_url: str | None = None,
) -> str | None:
    """Log a distributed multi-cause investigation to AIOps MLFlow.

    Extends log_aiops_run with multi-cause metadata.
    """
    if not MLFLOW_AVAILABLE:
        log.info(f"[mlflow-stub] log_distributed_run: model={model_id}, "
                 f"causes={causes_found}/{total_causes}")
        return None

    url = mlflow_url or get_mlflow_aiops_url()
    try:
        mlflow.set_tracking_uri(url)
        mlflow.set_experiment(AIOPS_EXPERIMENT)

        with mlflow.start_run() as run:
            # Standard parameters
            mlflow.log_param("model_id", model_id)
            mlflow.log_param("scenario", scenario)
            mlflow.log_param("tool_count", len(tool_calls))
            mlflow.log_param("timestamp", datetime.now(timezone.utc).isoformat())
            mlflow.log_param("scenario_type", "distributed")

            # Distributed-specific parameters
            mlflow.log_param("stagger_seconds", stagger_seconds)
            if fault1_time:
                mlflow.log_param("fault1_time", fault1_time)
            if fault2_time:
                mlflow.log_param("fault2_time", fault2_time)

            # Metrics
            if investigation_time_seconds is not None:
                mlflow.log_metric("investigation_time_seconds", investigation_time_seconds)
            mlflow.log_metric("tool_calls_total", len(tool_calls))
            mlflow.log_metric("causes_found", causes_found)
            mlflow.log_metric("total_causes", total_causes)
            mlflow.log_metric("rca_completeness", rca_completeness)

            # Tool type counts
            tool_type_counts: dict[str, int] = {}
            for tc in tool_calls:
                t = tc.get("tool", "unknown")
                tool_type_counts[t] = tool_type_counts.get(t, 0) + 1
            for tool_type, count in tool_type_counts.items():
                mlflow.log_metric(f"tool_{tool_type}_count", count)
            mlflow.log_metric("tool_types_used", len(tool_type_counts))

            # RCA output
            if rca_output:
                rca_ranked = rca_output.get("rca_ranked", [])
                mlflow.log_param("top_hypothesis", rca_ranked[0] if rca_ranked else "none")
                mlflow.log_metric("hypothesis_count", len(rca_ranked))
                mlflow.log_text(
                    json.dumps(rca_output, indent=2, default=str),
                    "aiops_output/rca_output.json",
                )

            if tool_calls:
                mlflow.log_text(
                    json.dumps(tool_calls, indent=2, default=str),
                    "tool_calls/tool_calls.json",
                )

            run_id = run.info.run_id
            log.info(f"[mlflow] Logged distributed run: {run_id} (model={model_id}, "
                     f"causes={causes_found}/{total_causes})")
            return run_id

    except Exception as e:
        log.warning(f"[mlflow] Failed to log distributed run (continuing): {e}")
        return None


# ---------------------------------------------------------------------------
# MTTD Logging
# ---------------------------------------------------------------------------

def log_mttd(
    inject_time: datetime,
    detect_time: datetime,
    scenario: str,
    mlflow_url: str | None = None,
) -> float:
    """Log MTTD (Mean Time to Detection) to the AIOps MLFlow instance.

    MTTD = detect_time - inject_time, measured in seconds.

    Returns MTTD in seconds.
    """
    mttd_seconds = (detect_time - inject_time).total_seconds()

    if not MLFLOW_AVAILABLE:
        log.info(f"[mlflow-stub] log_mttd: scenario={scenario}, mttd={mttd_seconds:.1f}s")
        return mttd_seconds

    url = mlflow_url or get_mlflow_aiops_url()
    try:
        mlflow.set_tracking_uri(url)
        mlflow.set_experiment(AIOPS_EXPERIMENT)

        with mlflow.start_run() as run:
            mlflow.log_param("scenario", scenario)
            mlflow.log_param("metric_type", "mttd")
            mlflow.log_param("inject_time", inject_time.isoformat())
            mlflow.log_param("detect_time", detect_time.isoformat())
            mlflow.log_metric("mttd_seconds", mttd_seconds)

            log.info(f"[mlflow] Logged MTTD: {mttd_seconds:.1f}s for {scenario}")

    except Exception as e:
        log.warning(f"[mlflow] Failed to log MTTD (continuing): {e}")

    return mttd_seconds
