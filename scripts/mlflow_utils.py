"""MLFlow experiment tracking utilities for the AIOps Harness.

Two MLFlow instances track different aspects of the system:

  1. AIOps Pipeline Tracker (mlflow-aiops):
     Tracks how the AIOps pipeline investigates incidents.
     Each Llama Stack agent session = one MLFlow run.

  2. Harness Evaluation Tracker (mlflow-harness):
     Tracks how well the pipeline investigated, based on harness scoring.
     Each harness evaluation = one MLFlow run.

This separation maintains the External Independence Principle:
the pipeline team sees investigation metrics, the evaluation team
sees scoring metrics, and neither can influence the other.

Usage:
    from mlflow_utils import log_aiops_run, log_harness_eval

    # After AIOps pipeline completes investigation
    log_aiops_run(
        mlflow_url="http://mlflow-aiops.mlflow-aiops.svc:5000",
        model_id="granite-4",
        scenario="cpu-saturation-reviews",
        tool_calls=[...],
        mttd_seconds=134.5,
        rca_output={...},
    )

    # After harness completes evaluation
    log_harness_eval(
        mlflow_url="http://mlflow-harness.mlflow-harness.svc:5000",
        run_id="run-20260223T174643Z",
        scores={"detection": 0.9, "correlation": 0.8, ...},
        judge_matrix={...},
        fact_check_results={...},
        result="PASS",
    )
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

# TODO: Uncomment when mlflow is installed
# import mlflow

# Default MLFlow tracking URIs
MLFLOW_AIOPS_URL = os.environ.get(
    "MLFLOW_AIOPS_URL", "http://mlflow-aiops.mlflow-aiops.svc:5000"
)
MLFLOW_HARNESS_URL = os.environ.get(
    "MLFLOW_HARNESS_URL", "http://mlflow-harness.mlflow-harness.svc:5000"
)

AIOPS_EXPERIMENT_NAME = "aiops-rca-pipeline"
HARNESS_EXPERIMENT_NAME = "aiops-harness-evaluation"


def log_aiops_run(
    mlflow_url: str,
    model_id: str,
    scenario: str,
    tool_calls: list[dict[str, Any]],
    mttd_seconds: float | None = None,
    rca_output: dict[str, Any] | None = None,
    investigation_time_seconds: float | None = None,
) -> str:
    """Log an AIOps pipeline investigation run to the AIOps MLFlow instance.

    Tracks the pipeline's behavior: what model was used, what tools were
    called, how long the investigation took, and what the pipeline concluded.

    Args:
        mlflow_url: MLFlow tracking server URL for the AIOps instance.
        model_id: Model identifier (e.g., "granite-4", "qwen3-coder-next").
        scenario: Scenario identifier (e.g., "cpu-saturation-reviews").
        tool_calls: List of tool call records from the agent session.
        mttd_seconds: Mean Time to Detection in seconds (alert-triggered mode).
        rca_output: The pipeline's aiops_output.json content.
        investigation_time_seconds: Total investigation wall-clock time.

    Returns:
        The MLFlow run ID.
    """
    # TODO: Implement MLFlow logging
    #
    # mlflow.set_tracking_uri(mlflow_url)
    # mlflow.set_experiment(AIOPS_EXPERIMENT_NAME)
    #
    # with mlflow.start_run() as run:
    #     # Parameters (what was configured)
    #     mlflow.log_param("model_id", model_id)
    #     mlflow.log_param("scenario", scenario)
    #     mlflow.log_param("tool_count", len(tool_calls))
    #     mlflow.log_param("timestamp", datetime.utcnow().isoformat())
    #
    #     # Metrics (what happened)
    #     if mttd_seconds is not None:
    #         mlflow.log_metric("mttd_seconds", mttd_seconds)
    #     if investigation_time_seconds is not None:
    #         mlflow.log_metric("investigation_time_seconds", investigation_time_seconds)
    #     mlflow.log_metric("tool_calls_total", len(tool_calls))
    #
    #     # Per-tool latency
    #     for i, tc in enumerate(tool_calls):
    #         if "latency_ms" in tc:
    #             mlflow.log_metric(f"tool_{tc['tool']}_latency_ms", tc["latency_ms"], step=i)
    #
    #     # Artifacts (full investigation output)
    #     if rca_output:
    #         mlflow.log_dict(rca_output, "aiops_output.json")
    #     mlflow.log_dict({"tool_calls": tool_calls}, "tool_calls.json")
    #
    #     return run.info.run_id

    print(f"[STUB] log_aiops_run: model={model_id}, scenario={scenario}, "
          f"tools={len(tool_calls)}, mttd={mttd_seconds}")
    return "stub-run-id"


def log_harness_eval(
    mlflow_url: str,
    run_id: str,
    scores: dict[str, float],
    judge_matrix: dict[str, dict[str, Any]] | None = None,
    fact_check_results: dict[str, Any] | None = None,
    result: str = "UNKNOWN",
    weighted_score: float | None = None,
) -> str:
    """Log a harness evaluation to the Harness MLFlow instance.

    Tracks evaluation results: how well the pipeline investigated,
    whether its claims held up under fact-checking, and the overall
    PASS/FAIL determination.

    Args:
        mlflow_url: MLFlow tracking server URL for the Harness instance.
        run_id: Harness run ID (e.g., "run-20260223T174643Z").
        scores: Category scores dict (detection, correlation, rca_detected,
                rca_eval, action_safety, auditability).
        judge_matrix: Cross-model peer evaluation matrix.
        fact_check_results: Which pipeline claims the eval model confirmed
                          vs. contradicted.
        result: PASS or FAIL.
        weighted_score: Weighted composite score.

    Returns:
        The MLFlow run ID.
    """
    # TODO: Implement MLFlow logging
    #
    # mlflow.set_tracking_uri(mlflow_url)
    # mlflow.set_experiment(HARNESS_EXPERIMENT_NAME)
    #
    # with mlflow.start_run() as run:
    #     # Parameters
    #     mlflow.log_param("harness_run_id", run_id)
    #     mlflow.log_param("result", result)
    #     mlflow.log_param("timestamp", datetime.utcnow().isoformat())
    #
    #     # Scoring metrics
    #     for dimension, score in scores.items():
    #         mlflow.log_metric(f"score_{dimension}", score)
    #     if weighted_score is not None:
    #         mlflow.log_metric("weighted_score", weighted_score)
    #
    #     # Artifacts
    #     if judge_matrix:
    #         mlflow.log_dict(judge_matrix, "judge_matrix.json")
    #     if fact_check_results:
    #         mlflow.log_dict(fact_check_results, "fact_check_results.json")
    #
    #     return run.info.run_id

    print(f"[STUB] log_harness_eval: run={run_id}, result={result}, "
          f"weighted={weighted_score}")
    return "stub-eval-id"


def log_mttd(
    mlflow_url: str,
    inject_time: datetime,
    detect_time: datetime,
    scenario: str,
) -> float:
    """Log MTTD (Mean Time to Detection) to the AIOps MLFlow instance.

    MTTD = detect_time - inject_time, measured in seconds.
    This is a supplementary operational metric (not a weighted scoring
    dimension) that tracks how quickly the monitoring infrastructure
    detects the injected fault.

    Args:
        mlflow_url: MLFlow tracking server URL.
        inject_time: Timestamp when the fault was injected.
        detect_time: Timestamp when the alert fired.
        scenario: Scenario identifier.

    Returns:
        MTTD in seconds.
    """
    mttd_seconds = (detect_time - inject_time).total_seconds()

    # TODO: Implement MLFlow logging
    #
    # mlflow.set_tracking_uri(mlflow_url)
    # mlflow.set_experiment(AIOPS_EXPERIMENT_NAME)
    #
    # with mlflow.start_run() as run:
    #     mlflow.log_param("scenario", scenario)
    #     mlflow.log_metric("mttd_seconds", mttd_seconds)
    #     mlflow.log_param("inject_time", inject_time.isoformat())
    #     mlflow.log_param("detect_time", detect_time.isoformat())

    print(f"[STUB] log_mttd: scenario={scenario}, mttd={mttd_seconds:.1f}s")
    return mttd_seconds
