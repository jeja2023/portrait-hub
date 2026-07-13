# PortraitHub Demo Clients

These examples are the two stage-two business demo clients referenced by the integration plan. They use the same service endpoint with different tenants so operators can verify application API keys, tenant isolation, SDK calls, call logs, SLO panels, and gateway routing without inventing client code during acceptance.

## Environment

```bash
export PORTRAIT_HUB_BASE_URL="https://portrait.internal.example"
export PORTRAIT_HUB_TENANT_ID="tenant-a"
export PORTRAIT_HUB_API_TOKEN="phk_..."
export PORTRAIT_HUB_AUTH_SCHEME="api_key"
```

Use `tenant-a` for the Python demo and `tenant-b` for the Node demo when validating two independent business projects against the same service.

## Python Demo

```bash
python examples/demo-clients/python_demo_client.py --dry-run
python examples/demo-clients/python_demo_client.py --image samples/person-a.jpg --image-b samples/person-b.jpg --video samples/clip.mp4
```

## Node Demo

```bash
node examples/demo-clients/node_demo_client.js --dry-run
node examples/demo-clients/node_demo_client.js --image samples/person-a.jpg --image-b samples/person-b.jpg --video samples/clip.mp4
```

The demos always call `health`, `models`, and `thresholds`. When media paths are provided they also exercise `enroll`, `search`, `compare`, and `create_video_job` / `createVideoJob` through the SDK. Outputs include request IDs and response data keys, but never print the API key.