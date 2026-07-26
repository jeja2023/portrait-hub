# PortraitHub Python SDK

The Python SDK is the stable, officially supported client for the PortraitHub `/v1` API. It uses only the Python standard library and supports Python 3.10 or newer.

## Install

```bash
python -m pip install ./sdk/python
```

## First request

```python
import os

from portrait_hub_sdk import PortraitHubClient

client = PortraitHubClient(
    os.environ["PORTRAIT_HUB_URL"],
    api_token=os.environ["PORTRAIT_HUB_API_TOKEN"],
    auth_scheme="api_key",
    project_id=os.environ["PORTRAIT_HUB_PROJECT_ID"],
)

compatibility = client.check_compatibility()
result = client.search("query.jpg", top_k=5)
```

## Resumable video job

```python
result = client.upload_video_resumable(
    "entrance.mp4",
    chunk_size=8 * 1024 * 1024,
    priority=20,
)
job = client.wait_for_job(result["job"]["job_id"], timeout=900)
```

Pass the previous `upload_id` to `upload_video_resumable` after a network interruption. Uploaded parts with matching offsets and SHA-256 digests are skipped.

## Webhook verification

```python
verified = PortraitHubClient.verify_webhook_signature(
    raw_request_body,
    request.headers["X-PortraitHub-Signature"],
    webhook_secret,
    timestamp=request.headers["X-PortraitHub-Timestamp"],
)
```

Keep the raw request body for verification. Do not log API keys, webhook secrets, original biometric data, or full media URLs. Catch the specific `PortraitHubHTTPError` subclasses for authentication, permission, validation, conflict, rate-limit, and server failures.

See `CHANGELOG.md` and `COMPATIBILITY.md` before upgrading.

## Clean-environment integration smoke

Release and CI validation builds the wheel, installs it without dependencies in a new virtual environment, starts an isolated PortraitHub service, and verifies both health and `/v1` compatibility:

```bash
python tools/portrait_sdk_clean_smoke.py --json
```

Use `--base-url` to run the same installed-wheel check against an already running acceptance environment.
