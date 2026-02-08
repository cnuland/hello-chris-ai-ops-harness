"""Artifact storage — writes run bundle JSON files to the output directory."""

import json
import os
from pathlib import Path


OUTPUT_BASE = Path(os.environ.get("HARNESS_OUTPUT_DIR", "/outputs"))


def get_output_dir(run_id: str) -> Path:
    """Create and return the output directory for a run."""
    out = OUTPUT_BASE / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_artifact(run_id: str, filename: str, data: dict) -> str:
    """Write a JSON artifact to the run's output directory."""
    out_dir = get_output_dir(run_id)
    path = out_dir / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return str(path)


def write_all_artifacts(run_id: str, run: dict, truth: dict, aiops_output: dict, score: dict):
    """Write all four contract artifacts and print them to stdout for log extraction."""
    paths = {}
    for name, data in [
        ("run.json", run),
        ("truth.json", truth),
        ("aiops_output.json", aiops_output),
        ("score.json", score),
    ]:
        paths[name] = write_artifact(run_id, name, data)

    # Print artifacts to stdout with markers so they can be extracted from logs
    # This is needed because pod emptyDir volumes are lost after completion
    for name, data in [
        ("run.json", run),
        ("truth.json", truth),
        ("aiops_output.json", aiops_output),
        ("score.json", score),
    ]:
        print(f"===ARTIFACT_START:{name}===")
        print(json.dumps(data, indent=2, default=str))
        print(f"===ARTIFACT_END:{name}===")

    return paths
