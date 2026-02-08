# Testing

## Local lint (optional)
- Python formatting: ruff/black (if used)
- JSON schema validation for run/truth/score/aiops_output

## In-cluster validation
1) Deploy: `./scripts/10_deploy_all.sh`
2) Verify Bookinfo healthy:
   - `oc -n bookinfo get pods`
   - `oc -n bookinfo get routes` (if route is created)
3) Verify tools server:
   - `oc -n aiops-harness get svc`
4) Run CPU harness and verify outputs exist:
   - artifacts include run.json/truth.json/aiops_output.json/score.json
5) Run CrashLoop harness and verify:
   - CrashLoopBackOff observed
   - truth packet matches injected fault
   - scorecard produced

## Pass criteria
- Both harness scenarios run end-to-end and generate contract-compliant artifacts.
