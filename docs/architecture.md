# Architecture

- Evidence Plane: Prometheus + OpenTelemetry Collector (+ optional logs/traces)
- AIOps Plane: Llama Stack agent exposed via tool endpoints
- Harness Plane: orchestrator handles injection, evidence capture, scoring
- Policy Gate: read-only by default; remediation requires explicit approval

See `docs/harness-contract.md` for artifact contracts and schemas.
