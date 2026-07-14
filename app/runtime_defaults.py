from __future__ import annotations

from pathlib import Path


DEFAULT_TRUSTED_HOSTS = "127.0.0.1,localhost"
DEFAULT_RATE_LIMIT_PER_MINUTE = 0
DEFAULT_RATE_LIMIT_BURST = 0
DEFAULT_RATE_LIMIT_MAX_BUCKETS = 10_000
DEFAULT_RATE_LIMIT_BUCKET_TTL_SECONDS = 3600
DEFAULT_MAX_REQUEST_BODY_BYTES = 768 * 1024 * 1024
DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"
)
DEFAULT_HSTS_ENABLED = False
DEFAULT_HSTS_MAX_AGE_SECONDS = 31_536_000
DEFAULT_HSTS_INCLUDE_SUBDOMAINS = True
DEFAULT_HSTS_PRELOAD = False

LOCAL_DEV_PATH_KEYS = {
    "MODEL_CONFIG_PATH": "models.yml",
    "MODEL_CAPABILITIES_PATH": "model-capabilities.yml",
}

LOCAL_DEV_STATE_PATH_KEYS = {
    "ROLLOUT_AUDIT_PATH": "rollout-audit.jsonl",
    "PORTRAIT_GALLERY_STATE_PATH": "portrait-gallery.json",
    "PORTRAIT_THRESHOLDS_STATE_PATH": "portrait-thresholds.json",
    "PORTRAIT_AUDIT_PATH": "portrait-audit.jsonl",
    "PORTRAIT_JOBS_STATE_PATH": "portrait-jobs.json",
    "PORTRAIT_IMAGE_RESULTS_STATE_PATH": "portrait-image-results.json",
    "PORTRAIT_STREAMS_STATE_PATH": "portrait-streams.json",
    "TASK_QUEUE_STATE_PATH": "portrait-task-queue.jsonl",
    "STREAM_EVENT_STATE_PATH": "portrait-stream-events.jsonl",
}

LOCAL_DEV_ENV_DEFAULTS = {
    "API_TOKEN": "",
    "AUTH_REQUIRED": "false",
    "RBAC_ENABLED": "false",
    "TENANT_HEADER_REQUIRED": "false",
    "ENABLE_API_DOCS": "true",
    "REQUIRE_ENCRYPTION": "false",
    "TRUSTED_HOSTS": DEFAULT_TRUSTED_HOSTS,
}


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def local_dev_env_overrides(root_dir: Path) -> dict[str, str]:
    runtime_state_dir = root_dir / "runtime-state"
    overrides = dict(LOCAL_DEV_ENV_DEFAULTS)
    overrides.update(
        {
            key: str(root_dir / relative_path)
            for key, relative_path in LOCAL_DEV_PATH_KEYS.items()
        }
    )
    overrides.update(
        {
            "RUNTIME_STATE_DIR": str(runtime_state_dir),
            "OBJECT_STORAGE_DIR": str(runtime_state_dir / "objects"),
            "VIDEO_JOB_INPUT_DIR": str(runtime_state_dir / "video-job-inputs"),
            "TASK_QUEUE_DIR": str(runtime_state_dir / "task-queue"),
            "STREAM_WORKER_LOCK_DIR": str(runtime_state_dir / "stream-worker-locks"),
        }
    )
    overrides.update(
        {
            key: str(runtime_state_dir / relative_path)
            for key, relative_path in LOCAL_DEV_STATE_PATH_KEYS.items()
        }
    )
    return overrides
