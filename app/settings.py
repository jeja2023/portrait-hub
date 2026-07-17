import os
from pathlib import Path
from typing import Any

from app.runtime_defaults import (
    DEFAULT_CONTENT_SECURITY_POLICY,
    DEFAULT_HSTS_ENABLED,
    DEFAULT_HSTS_INCLUDE_SUBDOMAINS,
    DEFAULT_HSTS_MAX_AGE_SECONDS,
    DEFAULT_HSTS_PRELOAD,
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_RATE_LIMIT_BUCKET_TTL_SECONDS,
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_MAX_BUCKETS,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    DEFAULT_TRUSTED_HOSTS,
)


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


APP_VERSION = "0.10.0"
PORTRAIT_RUNTIME_PROFILE = (
    os.getenv("PORTRAIT_RUNTIME_PROFILE", os.getenv("APP_ENV", "development")).strip().lower() or "development"
)
PRODUCTION_EXTERNAL_SERVICES_REQUIRED = parse_bool_env("PRODUCTION_EXTERNAL_SERVICES_REQUIRED", True)
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
VIDEO_UPLOAD_CHUNK_BYTES = parse_int_env("VIDEO_UPLOAD_CHUNK_BYTES", 1024 * 1024)
INFERENCE_BATCH_SIZE_LIMIT = 256
VIDEO_SAMPLE_INTERVAL_SECONDS = max(0.01, parse_float_env("VIDEO_SAMPLE_INTERVAL_SECONDS", 1.0))
VIDEO_INFERENCE_BATCH_SIZE = max(1, min(parse_int_env("VIDEO_INFERENCE_BATCH_SIZE", 16), INFERENCE_BATCH_SIZE_LIMIT))
MAX_VIDEO_FRAME_UPLOADS = max(1, parse_int_env("MAX_VIDEO_FRAME_UPLOADS", 64))
VIDEO_JOB_MAX_RETRIES = parse_int_env("VIDEO_JOB_MAX_RETRIES", 2)
VIDEO_JOB_RETRY_BACKOFF_SECONDS = parse_float_env("VIDEO_JOB_RETRY_BACKOFF_SECONDS", 0.25)
# 每帧进度更新最多按此间隔持久化。终态（完成/取消/失败/重试）始终立即落盘，因此这里
# 只节流那些原本每帧都会重写整个任务状态文件（或整行 JSONB）的中间进度写入。<=0 关闭节流。
VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS = parse_float_env("VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS", 2.0)
STREAM_SAMPLE_INTERVAL_SECONDS = max(0.01, parse_float_env("STREAM_SAMPLE_INTERVAL_SECONDS", 1.0))
STREAM_INFERENCE_BATCH_SIZE = max(1, min(parse_int_env("STREAM_INFERENCE_BATCH_SIZE", 8), INFERENCE_BATCH_SIZE_LIMIT))
STREAM_READ_TIMEOUT_SECONDS = parse_int_env("STREAM_READ_TIMEOUT_SECONDS", 10)
STREAM_WORKER_POLL_INTERVAL_SECONDS = parse_float_env("STREAM_WORKER_POLL_INTERVAL_SECONDS", 5.0)
STREAM_WORKER_MAX_RECONNECTS = parse_int_env("STREAM_WORKER_MAX_RECONNECTS", 3)
STREAM_WORKER_LEASE_TTL_SECONDS = parse_float_env("STREAM_WORKER_LEASE_TTL_SECONDS", 30.0)
STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS = parse_float_env(
    "STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS", max(300.0, STREAM_WORKER_LEASE_TTL_SECONDS * 10.0)
)
# 视频/流解码后端：auto（装了 PyAV 就用、否则 OpenCV）/ opencv / pyav。
# PyAV 提供帧精确的单遍顺序解码；任何后端不可用或出错都会优雅回退到 OpenCV。
VIDEO_DECODE_BACKEND = os.getenv("VIDEO_DECODE_BACKEND", "auto").strip().lower() or "auto"
MAX_VISION_IMAGES = parse_int_env("MAX_VISION_IMAGES", 16)
MAX_COMPARE_BATCH_PAIRS = parse_int_env("MAX_COMPARE_BATCH_PAIRS", 64)
MAX_IMAGE_DECODE_CONCURRENCY = parse_int_env("MAX_IMAGE_DECODE_CONCURRENCY", 4)
MAX_GALLERY_SEARCH_BATCH_CONCURRENCY = parse_int_env("MAX_GALLERY_SEARCH_BATCH_CONCURRENCY", 4)
MAX_COMPARE_BATCH_CONCURRENCY = parse_int_env("MAX_COMPARE_BATCH_CONCURRENCY", 2)
# 共享的推理请求边界 / 默认值，此前散落硬编码在 person 与 vision 路由处理器中。
MAX_DETECTIONS = parse_int_env("MAX_DETECTIONS", 1000)
MAX_TOP_K = parse_int_env("MAX_TOP_K", 100)
DEFAULT_CONFIDENCE = parse_float_env("DEFAULT_CONFIDENCE", 0.25)
DEFAULT_IOU = parse_float_env("DEFAULT_IOU", 0.45)
DEFAULT_DETECTOR_PROJECT = os.getenv("DEFAULT_DETECTOR_PROJECT", "portrait_hub").strip() or "portrait_hub"
DEFAULT_DETECTOR_ARTIFACT = os.getenv("DEFAULT_DETECTOR_ARTIFACT", "yolov8n.onnx").strip() or "yolov8n.onnx"
DEFAULT_REID_ARTIFACT = os.getenv("DEFAULT_REID_ARTIFACT", "osnet_ibn_x1_0.onnx").strip() or "osnet_ibn_x1_0.onnx"
# 在重试失败的可用性运行时（人体特征提取/人员检测）之前的冷却秒数，
# 而不是将其一直锁定为禁用状态直至进程重启。
RUNTIME_CAPABILITY_RETRY_COOLDOWN_SECONDS = parse_float_env("RUNTIME_CAPABILITY_RETRY_COOLDOWN_SECONDS", 60.0)
# 主模型注册表加载失败后的冷却秒数：一个模型加载失败后，冷却窗口内的请求直接 503 快速失败，
# 而不是每次都重新走昂贵的 hash + create_session；窗口过后自动重试，实现加载失败的自愈。
# <=0 关闭冷却（保持“每次请求都重试”的旧行为）。
MODEL_LOAD_RETRY_COOLDOWN_SECONDS = parse_float_env("MODEL_LOAD_RETRY_COOLDOWN_SECONDS", 30.0)
MAX_REQUEST_BODY_BYTES = parse_int_env("MAX_REQUEST_BODY_BYTES", DEFAULT_MAX_REQUEST_BODY_BYTES)
ALLOW_STREAM_URLS = parse_bool_env("ALLOW_STREAM_URLS", False)
MAX_LOADED_MODELS = parse_int_env("MAX_LOADED_MODELS", 0)
GPU_QUEUE_LIMIT = parse_int_env("GPU_QUEUE_LIMIT", 1)
GPU_QUEUE_LIMIT_PER_DEVICE = parse_int_env("GPU_QUEUE_LIMIT_PER_DEVICE", GPU_QUEUE_LIMIT)
GPU_DEVICE_IDS = [
    int(item) for item in parse_csv_env("GPU_DEVICE_IDS", os.getenv("CUDA_VISIBLE_DEVICES", "0")) if item.isdigit()
] or [0]
CPU_FALLBACK_ENABLED = parse_bool_env("CPU_FALLBACK_ENABLED", True)
# 强制纯 CPU 推理：直接以 CPUExecutionProvider 建会话，跳过 CUDA-first 的探测与回退重建。
# onnxruntime-gpu 即使在无 CUDA 库的机器上也会把 CUDA 报成“可用”，导致 create_session
# 先建一次 CUDA 会话（内部已回退 CPU）、再因 active 无 CUDA 而丢弃重建，模型被加载两次。
# CPU-only 部署设为 true 可消除这次重复加载与显存峰值。
FORCE_CPU = parse_bool_env("FORCE_CPU", False)
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
PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH = Path(
    os.getenv(
        "PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH",
        str(RUNTIME_STATE_DIR / "portrait-analysis-archive.sqlite3"),
    )
)
ANALYSIS_ARCHIVE_ENABLED = parse_bool_env("ANALYSIS_ARCHIVE_ENABLED", True)
ANALYSIS_ARCHIVE_PREVIEW_MAX_SIDE = parse_int_env("ANALYSIS_ARCHIVE_PREVIEW_MAX_SIDE", 480)
VIDEO_JOB_INPUT_DIR = Path(os.getenv("VIDEO_JOB_INPUT_DIR", str(RUNTIME_STATE_DIR / "video-job-inputs")))
PORTRAIT_STREAMS_STATE_PATH = Path(
    os.getenv("PORTRAIT_STREAMS_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-streams.json"))
)
PORTRAIT_ACCESS_STATE_PATH = Path(
    os.getenv("PORTRAIT_ACCESS_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-access.json"))
)
ACCESS_STATS_FLUSH_INTERVAL_SECONDS = parse_float_env("ACCESS_STATS_FLUSH_INTERVAL_SECONDS", 5.0)
PORTRAIT_REVIEW_STATE_PATH = Path(
    os.getenv("PORTRAIT_REVIEW_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-review-annotations.json"))
)
PORTRAIT_ACCESS_KEY_ROTATION_GRACE_SECONDS = parse_float_env("PORTRAIT_ACCESS_KEY_ROTATION_GRACE_SECONDS", 300.0)
STREAM_WORKER_LOCK_DIR = Path(
    os.getenv("STREAM_WORKER_LOCK_DIR", str(PORTRAIT_STREAMS_STATE_PATH.parent / "stream-worker-locks"))
)
ALLOW_PRIVATE_STREAM_HOSTS = parse_bool_env("ALLOW_PRIVATE_STREAM_HOSTS", False)
STREAM_ALLOWED_HOSTS = [item.lower() for item in parse_csv_env("STREAM_ALLOWED_HOSTS")]
ALLOW_PRIVATE_WEBHOOK_HOSTS = parse_bool_env("ALLOW_PRIVATE_WEBHOOK_HOSTS", False)
WEBHOOK_ALLOWED_HOSTS = [item.lower() for item in parse_csv_env("WEBHOOK_ALLOWED_HOSTS")]
WARMUP_MODELS = [item.strip() for item in os.getenv("WARMUP_MODELS", "").split(",") if item.strip()]
# 启动预热失败时是否让整个进程启动失败。默认 false：预热是尽力而为，单个模型加载失败只记录
# 并继续预热其余模型，避免一个坏模型拖垮整个服务启动（其余可用模型仍能对外提供推理）。
# 严格部署可设为 true，要求所有预热模型必须成功才允许启动。
WARMUP_FAIL_FAST = parse_bool_env("WARMUP_FAIL_FAST", False)
API_TOKEN = os.getenv("API_TOKEN")
API_TOKEN_TENANT_ID = os.getenv("API_TOKEN_TENANT_ID", "").strip()
API_TOKEN_ALLOW_TENANT_OVERRIDE = parse_bool_env("API_TOKEN_ALLOW_TENANT_OVERRIDE", False)
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_SECRET_ID = os.getenv("JWT_SECRET_ID", "primary").strip()
JWT_SECRET_KEYRING = os.getenv("JWT_SECRET_KEYRING", "")
JWT_ISSUER = os.getenv("JWT_ISSUER", "portrait-hub")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "portrait-hub-api")
JWT_REQUIRE_EXP = parse_bool_env("JWT_REQUIRE_EXP", True)
JWT_REQUIRE_ISS = parse_bool_env("JWT_REQUIRE_ISS", True)
JWT_REQUIRE_AUD = parse_bool_env("JWT_REQUIRE_AUD", True)
# 安全敏感开关默认取“安全（fail-closed）”值，使得从未加载 .env / docker-compose 的
# 部署处于锁定状态而非完全开放。已提交的 .env(.example) 与 compose 文件仍会显式设置它们；
# 本地开发通过 dev_start.py、测试套件通过 tests/conftest.py 重新选用宽松值。
RBAC_ENABLED = parse_bool_env("RBAC_ENABLED", False)
AUTH_REQUIRED = parse_bool_env("AUTH_REQUIRED", True)
CONSOLE_WORKBENCH_V2 = parse_bool_env("CONSOLE_WORKBENCH_V2", True)
CONSOLE_DEVELOPER_V2 = parse_bool_env("CONSOLE_DEVELOPER_V2", True)
CONSOLE_ADMIN_V2 = parse_bool_env("CONSOLE_ADMIN_V2", True)
CONSOLE_DEFAULT_VERSION = os.getenv("CONSOLE_DEFAULT_VERSION", "next").strip().lower()
if CONSOLE_DEFAULT_VERSION not in {"legacy", "next"}:
    CONSOLE_DEFAULT_VERSION = "next"
CONSOLE_WORKBENCH_V2_PERCENT = max(0, min(parse_int_env("CONSOLE_WORKBENCH_V2_PERCENT", 0), 100))
CONSOLE_DEVELOPER_V2_PERCENT = max(0, min(parse_int_env("CONSOLE_DEVELOPER_V2_PERCENT", 0), 100))
CONSOLE_ADMIN_V2_PERCENT = max(0, min(parse_int_env("CONSOLE_ADMIN_V2_PERCENT", 0), 100))
CONSOLE_WORKBENCH_V2_TENANTS = parse_csv_env("CONSOLE_WORKBENCH_V2_TENANTS")
CONSOLE_DEVELOPER_V2_TENANTS = parse_csv_env("CONSOLE_DEVELOPER_V2_TENANTS")
CONSOLE_ADMIN_V2_TENANTS = parse_csv_env("CONSOLE_ADMIN_V2_TENANTS")
CONSOLE_WS_TICKET_TTL_SECONDS = max(5, min(parse_int_env("CONSOLE_WS_TICKET_TTL_SECONDS", 60), 300))
CONSOLE_WS_TICKET_MAX_ENTRIES = max(128, min(parse_int_env("CONSOLE_WS_TICKET_MAX_ENTRIES", 4096), 65_536))
DEBUG_ENDPOINTS_ENABLED = parse_bool_env("DEBUG_ENDPOINTS_ENABLED", False)
ENABLE_API_DOCS = parse_bool_env("ENABLE_API_DOCS", False)
TRUSTED_HOSTS = parse_csv_env("TRUSTED_HOSTS", DEFAULT_TRUSTED_HOSTS)
TENANT_HEADER_REQUIRED = parse_bool_env("TENANT_HEADER_REQUIRED", True)
JWT_REQUIRE_TENANT = parse_bool_env("JWT_REQUIRE_TENANT", True)
PORTRAIT_STORAGE_BACKEND = os.getenv("PORTRAIT_STORAGE_BACKEND", "json").strip().lower()
PORTRAIT_VECTOR_BACKEND = os.getenv("PORTRAIT_VECTOR_BACKEND", "local").strip().lower()
PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND = parse_bool_env("PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND", False)
PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES = parse_bool_env("PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES", False)
PORTRAIT_OBJECT_STORAGE_BACKEND = os.getenv("PORTRAIT_OBJECT_STORAGE_BACKEND", "local").strip().lower()
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
POSTGRES_CONNECT_TIMEOUT_SECONDS = parse_int_env("POSTGRES_CONNECT_TIMEOUT_SECONDS", 3)
POSTGRES_POOL_MIN_SIZE = parse_int_env("POSTGRES_POOL_MIN_SIZE", 1)
POSTGRES_POOL_MAX_SIZE = parse_int_env("POSTGRES_POOL_MAX_SIZE", 10)
# pgvector HNSW 候选列表大小。ANN 索引只按 embedding_dim 分区，因此 tenant_id/modality
# 的等值过滤是在 ANN 候选之上施加的；当许多 tenant 或 modality 共享同一维度时，更大的
# ef_search 可保持较高召回。实际生效值为 max(本值, top_k)。
PGVECTOR_HNSW_EF_SEARCH = parse_int_env("PGVECTOR_HNSW_EF_SEARCH", 100)
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
REQUIRE_ENCRYPTION = parse_bool_env("REQUIRE_ENCRYPTION", True)
TASK_QUEUE_BACKEND = os.getenv("TASK_QUEUE_BACKEND", "local").strip().lower()
TASK_QUEUE_STATE_PATH = Path(os.getenv("TASK_QUEUE_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-task-queue.jsonl")))
TASK_QUEUE_DIR = Path(os.getenv("TASK_QUEUE_DIR", str(RUNTIME_STATE_DIR / "task-queue")))
TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS = parse_float_env("TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS", 300.0)
TASK_QUEUE_POLL_INTERVAL_SECONDS = parse_float_env("TASK_QUEUE_POLL_INTERVAL_SECONDS", 0.5)
VIDEO_JOB_WORKER_IN_PROCESS = parse_bool_env("VIDEO_JOB_WORKER_IN_PROCESS", TASK_QUEUE_BACKEND in {"", "local"})
REDIS_URL = os.getenv("REDIS_URL", "")
STREAM_EVENT_STATE_PATH = Path(
    os.getenv("STREAM_EVENT_STATE_PATH", str(RUNTIME_STATE_DIR / "portrait-stream-events.jsonl"))
)
MODEL_CAPABILITIES_PATH = Path(os.getenv("MODEL_CAPABILITIES_PATH", "model-capabilities.yml"))
RATE_LIMIT_PER_MINUTE = parse_int_env("RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT_PER_MINUTE)
RATE_LIMIT_BURST = parse_int_env("RATE_LIMIT_BURST", DEFAULT_RATE_LIMIT_BURST)
RATE_LIMIT_MAX_BUCKETS = parse_int_env("RATE_LIMIT_MAX_BUCKETS", DEFAULT_RATE_LIMIT_MAX_BUCKETS)
RATE_LIMIT_BUCKET_TTL_SECONDS = parse_int_env("RATE_LIMIT_BUCKET_TTL_SECONDS", DEFAULT_RATE_LIMIT_BUCKET_TTL_SECONDS)
# 当服务运行在受信任的反向代理之后时，从最左侧的 X-Forwarded-For 条目中获取客户端 IP。
# 直接暴露时应保持禁用，以防通过伪造该请求头来为每次请求刷新限流令牌桶。
RATE_LIMIT_TRUST_FORWARDED_FOR = parse_bool_env("RATE_LIMIT_TRUST_FORWARDED_FOR", False)
SECURITY_HEADERS_ENABLED = parse_bool_env("SECURITY_HEADERS_ENABLED", True)
CONTENT_SECURITY_POLICY = os.getenv("CONTENT_SECURITY_POLICY", DEFAULT_CONTENT_SECURITY_POLICY).strip()
HSTS_ENABLED = parse_bool_env("HSTS_ENABLED", DEFAULT_HSTS_ENABLED)
HSTS_MAX_AGE_SECONDS = parse_int_env("HSTS_MAX_AGE_SECONDS", DEFAULT_HSTS_MAX_AGE_SECONDS)
HSTS_INCLUDE_SUBDOMAINS = parse_bool_env("HSTS_INCLUDE_SUBDOMAINS", DEFAULT_HSTS_INCLUDE_SUBDOMAINS)
HSTS_PRELOAD = parse_bool_env("HSTS_PRELOAD", DEFAULT_HSTS_PRELOAD)
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
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
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
