# Troubleshooting

## Cluster Access
- Ensure `oc whoami` returns a user; refresh token if expired.
- Verify required Roles/RoleBindings in `manifests/50-harness/rbac.yaml`.

## vLLM / GPU Issues

### "No platform detected, vLLM is running on UnspecifiedPlatform"
- **Cause**: Missing `runtimeClassName: nvidia` on the pod spec.
- **Fix**: Ensure the Deployment includes `spec.template.spec.runtimeClassName: nvidia`. Without this, CRI-O doesn't invoke the NVIDIA container runtime hook to expose GPU devices.

### Model download fails / "LocalEntryNotFoundError"
- **Cause**: `HF_HUB_OFFLINE=1` set in the RHAIIS image by default.
- **Fix**: Set `HF_HUB_OFFLINE=0` and `TRANSFORMERS_OFFLINE=0` as env vars.
- Also verify `HF_TOKEN` secret (`llm-d-hf-token`) is present in the `llm-serving` namespace.

### "PermissionError: /home/.cache"
- **Cause**: Default `HOME=/home` is not writable in non-root containers.
- **Fix**: Set `HOME=/tmp` as an environment variable.

### FP8 model errors (Marlin kernel "size_n not divisible by tile_n_size")
- **Cause**: A100 40GB GPUs don't have native FP8 support. The Marlin fallback kernel requires weight dimensions divisible by 64.
- **Fix**: Use the unquantized model (`ibm-granite/granite-4.0-h-tiny`) with `--dtype bfloat16` instead of FP8-quantized variants.

### Pod Pending / Unschedulable
- **Cause**: GPU nodes have taint `nvidia.com/gpu=true:NoSchedule` that must be tolerated.
- **Fix**: Add the GPU toleration to the pod spec:
  ```yaml
  tolerations:
    - key: nvidia.com/gpu
      operator: Equal
      value: "true"
      effect: NoSchedule
  ```

## Tool Calling

### 400 Bad Request from vLLM when agent is invoked
- **Cause**: vLLM requires explicit flags to enable tool calling.
- **Fix**: Add `--enable-auto-tool-choice --tool-call-parser hermes` to the vLLM serve command.

### Tool calls appear in text but not in structured `tool_calls` array
- **Cause**: Wrong tool call parser for the model.
- **Fix**: Use `--tool-call-parser hermes` for Granite models (not `granite`).

## Bookinfo
- Verify all pods: `oc -n bookinfo get pods` — all should be Ready before running harness scenarios.
- Traffic generator should be running to produce baseline metrics.

## Tools Server
- Check health: `oc -n aiops-harness exec deploy/aiops-tools-server -- curl -s http://localhost:8000/healthz`
- Check Prometheus connectivity: look for Thanos Querier URL errors in tools server logs.
- 422 errors on `searchLogs`: the pod name parameter may not match the actual pod name. This is a known minor issue.

## Artifacts
- If artifacts are missing from `oc cp`, the fetch script will extract them from pod logs.
- If both fail, check harness-runner Job logs: `oc logs -n aiops-harness job/<job-name>`
- Artifacts are tagged in logs with `===ARTIFACT_START:<name>===` markers.
