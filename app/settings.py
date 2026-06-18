import os
from pathlib import Path
from typing import Any


def parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_csv_env(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


APP_VERSION = "0.5.35"
MODELS_ROOT = Path(os.getenv("MODELS_ROOT", "models")).resolve()
MODEL_CONFIG_PATH = Path(os.getenv("MODEL_CONFIG_PATH", "models.yml"))
MODEL_CONFIG_READ_FAIL_CLOSED = parse_bool_env("MODEL_CONFIG_READ_FAIL_CLOSED", True)
MAX_TENSOR_ITEMS = parse_int_env("MAX_TENSOR_ITEMS", 12_582_912)
MAX_IMAGE_BYTES = parse_int_env("MAX_IMAGE_BYTES", 10 * 1024 * 1024)
MAX_IMAGE_PIXELS = parse_int_env("MAX_IMAGE_PIXELS", 25_000_000)
MAX_PERSON_FRAMES = parse_int_env("MAX_PERSON_FRAMES", 16)
MAX_EMBEDDING_IMAGES = parse_int_env("MAX_EMBEDDING_IMAGES", 64)
MAX_PIPELINE_FRAMES = parse_int_env("MAX_PIPELINE_FRAMES", 16)
MAX_VIDEO_BYTES = parse_int_env("MAX_VIDEO_BYTES", 100 * 1024 * 1024)
VIDEO_FRAME_INTERVAL = parse_int_env("VIDEO_FRAME_INTERVAL", 15)
MAX_VIDEO_FRAMES = parse_int_env("MAX_VIDEO_FRAMES", 64)
VIDEO_JOB_MAX_RETRIES = parse_int_env("VIDEO_JOB_MAX_RETRIES", 2)
VIDEO_JOB_RETRY_BACKOFF_SECONDS = parse_float_env("VIDEO_JOB_RETRY_BACKOFF_SECONDS", 0.25)
STREAM_FRAME_INTERVAL = parse_int_env("STREAM_FRAME_INTERVAL", 15)
MAX_STREAM_FRAMES = parse_int_env("MAX_STREAM_FRAMES", 32)
STREAM_READ_TIMEOUT_SECONDS = parse_int_env("STREAM_READ_TIMEOUT_SECONDS", 10)
STREAM_WORKER_POLL_INTERVAL_SECONDS = parse_float_env("STREAM_WORKER_POLL_INTERVAL_SECONDS", 5.0)
STREAM_WORKER_MAX_RECONNECTS = parse_int_env("STREAM_WORKER_MAX_RECONNECTS", 3)
MAX_VISION_IMAGES = parse_int_env("MAX_VISION_IMAGES", 16)
MAX_REQUEST_BODY_BYTES = parse_int_env("MAX_REQUEST_BODY_BYTES", 768 * 1024 * 1024)
ALLOW_STREAM_URLS = parse_bool_env("ALLOW_STREAM_URLS", False)
MAX_LOADED_MODELS = parse_int_env("MAX_LOADED_MODELS", 0)
GPU_QUEUE_LIMIT = parse_int_env("GPU_QUEUE_LIMIT", 1)
GPU_QUEUE_LIMIT_PER_DEVICE = parse_int_env("GPU_QUEUE_LIMIT_PER_DEVICE", GPU_QUEUE_LIMIT)
GPU_DEVICE_IDS = [
    int(item)
    for item in parse_csv_env("GPU_DEVICE_IDS", os.getenv("CUDA_VISIBLE_DEVICES", "0"))
    if item.isdigit()
] or [0]
CPU_FALLBACK_ENABLED = parse_bool_env("CPU_FALLBACK_ENABLED", True)
MODEL_CONCURRENCY_LIMIT = parse_int_env("MODEL_CONCURRENCY_LIMIT", 1)
MODEL_QUEUE_TIMEOUT_SECONDS = parse_float_env("MODEL_QUEUE_TIMEOUT_SECONDS", 0)
ENABLE_TENSORRT = parse_bool_env("ENABLE_TENSORRT", False)
TENSORRT_ENGINE_CACHE_ENABLE = parse_bool_env("TENSORRT_ENGINE_CACHE_ENABLE", True)
TENSORRT_ENGINE_CACHE_PATH = os.getenv("TENSORRT_ENGINE_CACHE_PATH", "/tmp/tensorrt-engine-cache")
ROLLOUT_AUDIT_PATH = Path(os.getenv("ROLLOUT_AUDIT_PATH", "rollout-audit.jsonl"))
RUNTIME_STATE_DIR = Path(os.getenv("RUNTIME_STATE_DIR", "runtime-state"))
PORTRAIT_GALLERY_STATE_PATH = Path(
    os.getenv("PORTRAIT_GALLERY_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-gallery.json"))
)
PORTRAIT_THRESHOLDS_STATE_PATH = Path(
    os.getenv("PORTRAIT_THRESHOLDS_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-thresholds.json"))
)
PORTRAIT_AUDIT_PATH = Path(os.getenv("PORTRAIT_AUDIT_PATH", str(RUNTIME_STATE_DIR / "portrait-audit.jsonl")))
AUDIT_WRITE_FAIL_CLOSED = parse_bool_env("AUDIT_WRITE_FAIL_CLOSED", True)
PORTRAIT_JOBS_STATE_PATH = Path(os.getenv("PORTRAIT_JOBS_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-jobs.json")))
PORTRAIT_STREAMS_STATE_PATH = Path(os.getenv("PORTRAIT_STREAMS_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-streams.json")))
ALLOW_PRIVATE_STREAM_HOSTS = parse_bool_env("ALLOW_PRIVATE_STREAM_HOSTS", False)
STREAM_ALLOWED_HOSTS = [item.lower() for item in parse_csv_env("STREAM_ALLOWED_HOSTS")]
WARMUP_MODELS = [
    item.strip()
    for item in os.getenv("WARMUP_MODELS", "").split(",")
    if item.strip()
]
API_TOKEN = os.getenv("API_TOKEN")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_SECRET_ID = os.getenv("JWT_SECRET_ID", "primary").strip()
JWT_SECRET_KEYRING = os.getenv("JWT_SECRET_KEYRING", "")
JWT_ISSUER = os.getenv("JWT_ISSUER", "portrait-hub")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "portrait-hub-api")
JWT_REQUIRE_EXP = parse_bool_env("JWT_REQUIRE_EXP", True)
JWT_REQUIRE_ISS = parse_bool_env("JWT_REQUIRE_ISS", True)
JWT_REQUIRE_AUD = parse_bool_env("JWT_REQUIRE_AUD", True)
RBAC_ENABLED = parse_bool_env("RBAC_ENABLED", False)
AUTH_REQUIRED = parse_bool_env("AUTH_REQUIRED", False)
DEBUG_ENDPOINTS_ENABLED = parse_bool_env("DEBUG_ENDPOINTS_ENABLED", False)
ENABLE_API_DOCS = parse_bool_env("ENABLE_API_DOCS", True)
TRUSTED_HOSTS = parse_csv_env("TRUSTED_HOSTS", "*")
TENANT_HEADER_REQUIRED = parse_bool_env("TENANT_HEADER_REQUIRED", False)
JWT_REQUIRE_TENANT = parse_bool_env("JWT_REQUIRE_TENANT", True)
PORTRAIT_STORAGE_BACKEND = os.getenv("PORTRAIT_STORAGE_BACKEND", "json").strip().lower()
PORTRAIT_VECTOR_BACKEND = os.getenv("PORTRAIT_VECTOR_BACKEND", "local").strip().lower()
PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND = parse_bool_env("PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND", False)
PORTRAIT_OBJECT_STORAGE_BACKEND = os.getenv("PORTRAIT_OBJECT_STORAGE_BACKEND", "local").strip().lower()
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
POSTGRES_CONNECT_TIMEOUT_SECONDS = parse_int_env("POSTGRES_CONNECT_TIMEOUT_SECONDS", 3)
POSTGRES_POOL_MIN_SIZE = parse_int_env("POSTGRES_POOL_MIN_SIZE", 1)
POSTGRES_POOL_MAX_SIZE = parse_int_env("POSTGRES_POOL_MAX_SIZE", 10)
QDRANT_URL = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_PREFER_GRPC = parse_bool_env("QDRANT_PREFER_GRPC", False)
OBJECT_STORAGE_DIR = Path(os.getenv("OBJECT_STORAGE_DIR", str(RUNTIME_STATE_DIR / "objects")))
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
ENCRYPTION_KEY_ID = os.getenv("ENCRYPTION_KEY_ID", "primary").strip()
ENCRYPTION_KEYRING = os.getenv("ENCRYPTION_KEYRING", "")
ENCRYPTION_KDF = os.getenv("ENCRYPTION_KDF", "pbkdf2-sha256").strip().lower()
ENCRYPTION_PBKDF2_ITERATIONS = parse_int_env("ENCRYPTION_PBKDF2_ITERATIONS", 210_000)
REQUIRE_ENCRYPTION = parse_bool_env("REQUIRE_ENCRYPTION", False)
TASK_QUEUE_BACKEND = os.getenv("TASK_QUEUE_BACKEND", "local").strip().lower()
TASK_QUEUE_STATE_PATH = Path(os.getenv("TASK_QUEUE_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-task-queue.jsonl")))
REDIS_URL = os.getenv("REDIS_URL", "")
STREAM_EVENT_STATE_PATH = Path(os.getenv("STREAM_EVENT_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-stream-events.jsonl")))
MODEL_CAPABILITIES_PATH = Path(os.getenv("MODEL_CAPABILITIES_PATH", "model-capabilities.yml"))
RATE_LIMIT_PER_MINUTE = parse_int_env("RATE_LIMIT_PER_MINUTE", 0)
RATE_LIMIT_BURST = parse_int_env("RATE_LIMIT_BURST", 0)
RATE_LIMIT_MAX_BUCKETS = parse_int_env("RATE_LIMIT_MAX_BUCKETS", 10_000)
RATE_LIMIT_BUCKET_TTL_SECONDS = parse_int_env("RATE_LIMIT_BUCKET_TTL_SECONDS", 3600)
SECURITY_HEADERS_ENABLED = parse_bool_env("SECURITY_HEADERS_ENABLED", True)
CONTENT_SECURITY_POLICY = os.getenv(
    "CONTENT_SECURITY_POLICY",
    "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
).strip()
HSTS_ENABLED = parse_bool_env("HSTS_ENABLED", False)
HSTS_MAX_AGE_SECONDS = parse_int_env("HSTS_MAX_AGE_SECONDS", 31_536_000)
HSTS_INCLUDE_SUBDOMAINS = parse_bool_env("HSTS_INCLUDE_SUBDOMAINS", True)
HSTS_PRELOAD = parse_bool_env("HSTS_PRELOAD", False)
DATA_RETENTION_DAYS = parse_int_env("DATA_RETENTION_DAYS", 0)
STATE_READ_FAIL_CLOSED = parse_bool_env("STATE_READ_FAIL_CLOSED", True)
STATE_WRITE_FAIL_CLOSED = parse_bool_env("STATE_WRITE_FAIL_CLOSED", True)
MAX_PUBLIC_METADATA_BYTES = parse_int_env("MAX_PUBLIC_METADATA_BYTES", 16_384)
MAX_PUBLIC_METADATA_DEPTH = parse_int_env("MAX_PUBLIC_METADATA_DEPTH", 6)
MAX_PUBLIC_METADATA_KEYS = parse_int_env("MAX_PUBLIC_METADATA_KEYS", 128)
MAX_PUBLIC_METADATA_STRING_LENGTH = parse_int_env("MAX_PUBLIC_METADATA_STRING_LENGTH", 2048)
MAX_AUDIT_PAYLOAD_BYTES = parse_int_env("MAX_AUDIT_PAYLOAD_BYTES", 32_768)
MAX_AUDIT_DEPTH = parse_int_env("MAX_AUDIT_DEPTH", 6)
MAX_AUDIT_KEYS = parse_int_env("MAX_AUDIT_KEYS", 128)
MAX_AUDIT_LIST_ITEMS = parse_int_env("MAX_AUDIT_LIST_ITEMS", 64)
MAX_AUDIT_STRING_LENGTH = parse_int_env("MAX_AUDIT_STRING_LENGTH", 2048)
API_LIST_DEFAULT_LIMIT = parse_int_env("API_LIST_DEFAULT_LIMIT", 100)
MAX_API_LIST_LIMIT = parse_int_env("MAX_API_LIST_LIMIT", 500)
STREAM_EVENT_LIST_DEFAULT_LIMIT = parse_int_env("STREAM_EVENT_LIST_DEFAULT_LIMIT", 100)
MAX_STREAM_EVENT_LIST_LIMIT = parse_int_env("MAX_STREAM_EVENT_LIST_LIMIT", 200)
PORTRAIT_GALLERY_WAL_ENABLED = parse_bool_env("PORTRAIT_GALLERY_WAL_ENABLED", True)
PORTRAIT_GALLERY_WAL_COMPACT_EVERY = parse_int_env("PORTRAIT_GALLERY_WAL_COMPACT_EVERY", 250)
PROMETHEUS_METRICS_CACHE_SECONDS = parse_float_env("PROMETHEUS_METRICS_CACHE_SECONDS", 5.0)
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256").strip().upper() or "HS256"
JWT_PUBLIC_KEY = os.getenv("JWT_PUBLIC_KEY", "")
JWT_PUBLIC_KEY_PATH = os.getenv("JWT_PUBLIC_KEY_PATH", "")
JWT_PUBLIC_KEYRING = os.getenv("JWT_PUBLIC_KEYRING", "")
OPENTELEMETRY_ENABLED = parse_bool_env("OPENTELEMETRY_ENABLED", False)
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "portrait-hub")
CONFIG_HOT_RELOAD_ENABLED = parse_bool_env("CONFIG_HOT_RELOAD_ENABLED", True)
READY_CHECK_DEPENDENCIES = parse_bool_env("READY_CHECK_DEPENDENCIES", False)

CPU_PROVIDERS: list[Any] = ["CPUExecutionProvider"]
CUDA_PROVIDERS: list[Any] = [
    (
        "CUDAExecutionProvider",
        {
            "device_id": 0,
            "arena_extend_strategy": "kNextPowerOfTwo",
            "gpu_mem_limit": 0,
            "cudnn_conv_algo_search": "EXHAUSTIVE",
            "do_copy_in_default_stream": True,
        },
    ),
    *CPU_PROVIDERS,
]
