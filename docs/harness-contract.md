# Harness Contract

Artifacts per run:
- `run.json` — run metadata, scenario, timestamps, environment
- `truth.json` — ground truth for the injected incident
- `aiops_output.json` — agent output plus tool-call log and evidence pointers
- `score.json` — rubric-based evaluation of the agent output

Schemas are in `harness/schemas/` and should be enforced during CI.
