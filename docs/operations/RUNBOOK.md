# PortraitHub Runbook

When alerts fire:

1. Check `/ready` and `/ready/deep`.
2. Review fast-burn and slow-burn error budget alerts.
3. Inspect the latest rollout audit entry.
4. Roll back the active model alias if the regression is tied to a model change.
5. Reduce traffic or add capacity if latency and queue depth are growing without a bad model release.

Synthetic probe:

- Run a small authenticated request through the API.
- Confirm the request ID and the probe latency are logged.

Capacity test:

- Run the load test before traffic jumps.
- Track p95 latency, queue depth, and GPU memory.
