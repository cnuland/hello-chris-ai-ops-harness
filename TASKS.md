# Tasks

## MVP (must work)
- [ ] Deploy namespaces and RBAC
- [ ] Deploy OpenTelemetry Collector (basic pipeline)
- [ ] Deploy Bookinfo + traffic generator
- [ ] Deploy tools server (at least Prometheus + k8s events)
- [ ] Deploy Llama Stack (manifest + agent config stub)
- [ ] Implement harness runner Job:
  - [ ] Load HarnessManifest
  - [ ] Baseline window
  - [ ] Inject fault (CPU or CrashLoop)
  - [ ] Collect evidence pointers
  - [ ] Invoke Llama Stack agent (tool-based)
  - [ ] Emit run.json/truth.json/aiops_output.json/score.json
- [ ] Scripts to deploy and run scenarios

## Enhancements (nice to have)
- [ ] Add logs backend integration (OpenShift Logging / Loki) to searchLogs
- [ ] Add traces integration (Tempo/Jaeger) to getTraceWaterfall
- [ ] Store artifacts to S3 (optional)
- [ ] Add an OpenShift AI pipeline run that replays EVAL runs and trends scores
