"""API 限界与租户隔离门禁：列表分页上限、数值参数边界、租户契约、限流与请求体上限。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_api_limits_and_tenant(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    settings = src["settings"]
    portrait_pagination = src["portrait_pagination"]
    portrait_stream_routes = src["portrait_stream_routes"]
    portrait_admin_routes = src["portrait_admin_routes"]
    compose = src["compose"]
    env_example = src["env_example"]
    portrait_request_validation = src["portrait_request_validation"]
    portrait_gallery_routes = src["portrait_gallery_routes"]
    portrait_job_routes = src["portrait_job_routes"]
    portrait_security = src["portrait_security"]
    portrait_gallery = src["portrait_gallery"]
    portrait_gallery_records = src["portrait_gallery_records"]
    portrait_jobs = src["portrait_jobs"]
    portrait_streams = src["portrait_streams"]
    portrait_task_queue = src["portrait_task_queue"]
    media_schema = src["media_schema"]
    video_io = src["video_io"]
    portrait_stream_worker = src["portrait_stream_worker"]
    inference_classification = src["inference_classification"]
    inference_detection = src["inference_detection"]
    vision_routes = src["vision_routes"]
    portrait_object_storage = src["portrait_object_storage"]
    stream_decode = src["stream_decode"]
    portrait_gallery_route_orchestration = src["portrait_gallery_route_orchestration"]
    portrait_thresholds = src["portrait_thresholds"]
    portrait_compare_routes = src["portrait_compare_routes"]
    portrait_model_routes = src["portrait_model_routes"]
    schemas = src["schemas"]
    rate_limit = src["rate_limit"]
    portrait_gallery_impl = src["portrait_gallery_impl"]
    server = src["server"]
    cpu_dockerfile = src["cpu_dockerfile"]
    cpu_compose = src["cpu_compose"]
    return [
        {
            "name": "security:bounded_api_list_responses",
            "ok": (
                "API_LIST_DEFAULT_LIMIT" in settings
                and "MAX_API_LIST_LIMIT" in settings
                and "STREAM_EVENT_LIST_DEFAULT_LIMIT" in settings
                and "MAX_STREAM_EVENT_LIST_LIMIT" in settings
                and "def normalize_list_pagination" in portrait_pagination
                and "def normalize_stream_event_pagination" in portrait_pagination
                and "def page_items" in portrait_pagination
                and "def page_items_keyset" in portrait_pagination
                and '"next_offset"' in portrait_pagination
                and '"next_cursor"' in portrait_pagination
                and "normalize_list_pagination" in portrait_stream_routes
                and "normalize_stream_event_pagination" in portrait_stream_routes
                and "page_items_keyset(" in portrait_stream_routes
                and 'key_fields=["stream_id"]' in portrait_stream_routes
                and 'key_fields=["created_at", "event_id"]' in portrait_stream_routes
                and "normalize_list_pagination" in portrait_admin_routes
                and "normalize_stream_event_pagination" in portrait_admin_routes
                and "page_items_keyset(" in portrait_admin_routes
                and '"pagination"' in portrait_admin_routes
                and '"events_pagination"' in portrait_admin_routes
                and "API_LIST_DEFAULT_LIMIT: ${API_LIST_DEFAULT_LIMIT:-100}" in compose
                and "MAX_API_LIST_LIMIT: ${MAX_API_LIST_LIMIT:-500}" in compose
                and "STREAM_EVENT_LIST_DEFAULT_LIMIT: ${STREAM_EVENT_LIST_DEFAULT_LIMIT:-100}"
                in compose
                and "MAX_STREAM_EVENT_LIST_LIMIT: ${MAX_STREAM_EVENT_LIST_LIMIT:-200}"
                in compose
                and "API_LIST_DEFAULT_LIMIT=100" in env_example
                and "MAX_API_LIST_LIMIT=500" in env_example
                and "STREAM_EVENT_LIST_DEFAULT_LIMIT=100" in env_example
                and "MAX_STREAM_EVENT_LIST_LIMIT=200" in env_example
            ),
        },
        {
            "name": "security:explicit_numeric_parameter_bounds",
            "ok": (
                "def validate_int_range" in portrait_request_validation
                and "isinstance(value, bool)" in portrait_request_validation
                and 'validate_int_range("top_k", top_k, minimum=1, maximum=100)'
                in portrait_gallery_routes
                and "sample_interval_seconds" in portrait_job_routes
                and "batch_size" in portrait_job_routes
                and "INFERENCE_BATCH_SIZE_LIMIT" in portrait_job_routes
                and "validate_detection_parameters(" in portrait_job_routes
                and "top_k = max(1, min(100" not in portrait_gallery_routes
                and "frame_interval" not in portrait_job_routes
                and "VIDEO_SAMPLE_INTERVAL_SECONDS: ${VIDEO_SAMPLE_INTERVAL_SECONDS:-1.0}" in compose
                and "VIDEO_INFERENCE_BATCH_SIZE: ${VIDEO_INFERENCE_BATCH_SIZE:-16}" in compose
                and "MAX_VIDEO_FRAME_UPLOADS: ${MAX_VIDEO_FRAME_UPLOADS:-64}" in compose
                and "STREAM_SAMPLE_INTERVAL_SECONDS: ${STREAM_SAMPLE_INTERVAL_SECONDS:-1.0}" in compose
                and "STREAM_INFERENCE_BATCH_SIZE: ${STREAM_INFERENCE_BATCH_SIZE:-8}" in compose
            ),
        },
        {
            "name": "security:tenant_header_contract",
            "ok": (
                "TENANT_HEADER_REQUIRED" in settings
                and "缺少 x-tenant-id 请求头" in portrait_security
                and 'request.url.path.startswith("/v1/")' in portrait_security
                and "TENANT_HEADER_REQUIRED: ${TENANT_HEADER_REQUIRED:-true}" in compose
                and "TENANT_HEADER_REQUIRED=true" in env_example
            ),
        },
        {
            "name": "security:tenant_person_id_validation",
            "ok": (
                "TENANT_PATTERN" in portrait_security
                and "PERSON_ID_PATTERN" in portrait_security
                and "RESOURCE_ID_PATTERN" in portrait_security
                and "def validate_job_id" in portrait_security
                and "def validate_stream_id" in portrait_security
                and "validate_job_id(job_id)" in portrait_job_routes
                and "validate_stream_id(stream_id)" in portrait_stream_routes
            ),
        },
        {
            "name": "security:gallery_structured_tenant_key",
            "ok": (
                (
                    "GalleryKey = tuple[str, str]" in portrait_gallery
                    or "GalleryKey = tuple[str, str]" in portrait_gallery_records
                )
                and (
                    "def gallery_key" in portrait_gallery
                    or "def gallery_key" in portrait_gallery_records
                )
            ),
        },
        {
            "name": "security:job_stream_structured_tenant_keys",
            "ok": (
                "JobKey = tuple[str, str]" in portrait_jobs
                and "def job_key" in portrait_jobs
                and "StreamKey = tuple[str, str]" in portrait_streams
                and "def stream_key" in portrait_streams
            ),
        },
        {
            "name": "security:public_response_redaction",
            "ok": (
                "redact_sensitive_fields(self.metadata)"
                in (portrait_gallery + portrait_gallery_records)
                and "redact_sensitive_fields(self.settings)" in portrait_streams
                and "redact_sensitive_fields(self.payload)" in portrait_streams
                and "redact_sensitive_fields(self.payload)" in portrait_task_queue
                and '"filename"' in portrait_security
                and "def to_dict(self, include_filename: bool = False, include_fingerprint: bool = False)"
                in media_schema
                and "if include_filename and self.filename is not None:" in media_schema
                and "if include_fingerprint and self.fingerprint is not None:"
                in media_schema
                and "if self.fingerprint is not None:" not in media_schema
                and 'SENSITIVE_VIDEO_METADATA_KEYS = {"filename", "video_bytes", "frame_fingerprints"}'
                in video_io
                and "def public_video_metadata" in video_io
                and "public_video_metadata(metadata)" in portrait_stream_worker
                and "public_video_job_result" in portrait_jobs
                and '"filename": filenames[index]' not in inference_classification
                and '"filename": filenames[index]' not in inference_detection
                and '"filename": filename' not in vision_routes
                and "object_record_metadata(object_type, filename)"
                in portrait_object_storage
                and "def public_object_info" in portrait_object_storage
            ),
        },
        {
            "name": "security:stream_event_state_redaction",
            "ok": (
                "from app.portrait_security import redact_sensitive_fields"
                in portrait_stream_worker
                and "persisted_payload = redact_sensitive_fields(payload or {})"
                in portrait_stream_worker
                and '"payload": persisted_payload' in portrait_stream_worker
                and '"payload": payload or {}' not in portrait_stream_worker
            ),
        },
        {
            "name": "security:stream_sensitive_state_protection",
            "ok": (
                "PROTECTED_STATE_VALUE_MARKER" in portrait_streams
                and "def protect_sensitive_state_fields" in portrait_streams
                and "def reveal_sensitive_state_fields" in portrait_streams
                and "is_sensitive_field(key)" in portrait_streams
                and "encrypt_bytes(raw)" in portrait_streams
                and "decrypt_bytes(protected_payload)" in portrait_streams
                and '"settings": protect_sensitive_state_fields(self.settings)'
                in portrait_streams
                and '"metadata": protect_sensitive_state_fields(self.metadata)'
                in portrait_streams
                and 'settings=reveal_sensitive_state_fields(payload.get("settings"))'
                in portrait_streams
                and 'metadata=reveal_sensitive_state_fields(payload.get("metadata"))'
                in portrait_streams
            ),
        },
        {
            "name": "security:stream_url_ssrf_and_secret_protection",
            "ok": (
                "import socket" in stream_decode
                and "def resolve_stream_host_addresses" in stream_decode
                and "socket.getaddrinfo" in stream_decode
                and "def reject_private_resolved_addresses" in stream_decode
                and "reject_private_resolved_addresses(parsed.hostname)"
                in stream_decode
                and "parsed.query" in stream_decode
                and "parsed.fragment" in stream_decode
                and "stream_url_protected" in portrait_streams
                and "def protect_stream_url" in portrait_streams
                and "def reveal_stream_url" in portrait_streams
                and 'payload.pop("stream_url", None)' in portrait_streams
                and "protect_stream_url(stream.stream_url)" in portrait_streams
                and "reveal_stream_url(protected_url)" in portrait_streams
            ),
        },
        {
            "name": "security:metadata_input_limits",
            "ok": (
                "normalize_public_metadata" in portrait_security
                and "MAX_PUBLIC_METADATA_BYTES" in settings
                and "MAX_PUBLIC_METADATA_BYTES" in compose
                and "MAX_PUBLIC_METADATA_BYTES=16384" in env_example
                and "normalize_public_metadata(parsed"
                in portrait_gallery_route_orchestration
                and "normalize_public_metadata(payload.settings"
                in portrait_stream_routes
            ),
        },
        {
            "name": "security:threshold_control_contract",
            "ok": (
                "SUPPORTED_THRESHOLD_PROFILES" in portrait_thresholds
                and "def validate_threshold_profile" in portrait_thresholds
                and 'detail="不支持的阈值方案"' in portrait_thresholds
                and "不支持的阈值方案: {profile}" not in portrait_thresholds
                and "validate_threshold_profile(threshold_profile)"
                in portrait_compare_routes
                and "validate_threshold_profile(threshold_profile)"
                in portrait_gallery_route_orchestration
                and "SUPPORTED_GALLERY_MODALITIES"
                in portrait_gallery_route_orchestration
                and "def validate_gallery_modality"
                in portrait_gallery_route_orchestration
                and (
                    "modality = validate_gallery_modality(modality)"
                    in portrait_gallery_route_orchestration
                    or "modality_key = validate_gallery_modality(modality)"
                    in portrait_gallery_route_orchestration
                )
                and 'detail="不支持的模态"' in portrait_gallery_route_orchestration
                and 'profile=result["profile"]' in portrait_model_routes
                and "def validate_threshold_modality" in portrait_thresholds
                and 'detail="不支持的模态"' in portrait_thresholds
                and "不支持的模态: {modality}" not in portrait_thresholds
                and "def validate_threshold_value" in portrait_thresholds
                and "isinstance(raw_value, bool)" in portrait_thresholds
                and "math.isfinite" in portrait_thresholds
            ),
        },
        {
            "name": "security:strict_mutation_request_schemas",
            "ok": (
                'ConfigDict(extra="forbid", protected_namespaces=())' in schemas
                and "class InferenceRequest(BaseModel)" in schemas
                and "class ModelRequest(BaseModel)" in schemas
                and "class WarmupRequest(BaseModel)" in schemas
                and "class AliasSwitchRequest(BaseModel)" in schemas
                and "class AliasRollbackRequest(BaseModel)" in schemas
                and "class AliasRolloutTarget(BaseModel)" in schemas
                and "class AliasWeightedRolloutRequest(BaseModel)" in schemas
                and schemas.count('ConfigDict(extra="forbid", protected_namespaces=())')
                >= 3
                and schemas.count('ConfigDict(extra="forbid")') >= 4
                and "class GalleryPatchRequest(BaseModel)" in portrait_gallery_routes
                and 'ConfigDict(extra="forbid")' in portrait_gallery_routes
                and "display_name: str | None = Field(default=None, max_length=256)"
                in portrait_gallery_routes
                and "payload: GalleryPatchRequest" in portrait_gallery_routes
                and "补丁请求体不能为空" in portrait_gallery_routes
                and "class ThresholdUpdateRequest(BaseModel)" in portrait_model_routes
                and 'ConfigDict(extra="forbid")' in portrait_model_routes
                and "reject_boolean_thresholds" in portrait_model_routes
                and "payload: ThresholdUpdateRequest" in portrait_model_routes
                and "class StreamCreateRequest(BaseModel)" in portrait_stream_routes
                and 'ConfigDict(extra="forbid")' in portrait_stream_routes
                and "class RetentionCleanupRequest(BaseModel)" in portrait_admin_routes
                and 'ConfigDict(extra="forbid")' in portrait_admin_routes
            ),
        },
        {
            "name": "security:rate_limit_bucket_bounds",
            "ok": (
                "RATE_LIMIT_MAX_BUCKETS" in settings
                and "RATE_LIMIT_PER_MINUTE" in settings
                and "RATE_LIMIT_BURST" in settings
                and "RATE_LIMIT_BUCKET_TTL_SECONDS" in settings
                and "def cleanup_idle_buckets" in rate_limit
                and "def ensure_bucket_capacity" in rate_limit
                and "限流桶容量已耗尽" in rate_limit
                and "RATE_LIMIT_PER_MINUTE: ${RATE_LIMIT_PER_MINUTE:-120}" in compose
                and "RATE_LIMIT_BURST: ${RATE_LIMIT_BURST:-240}" in compose
                and "RATE_LIMIT_MAX_BUCKETS: ${RATE_LIMIT_MAX_BUCKETS:-10000}"
                in compose
                and "RATE_LIMIT_BUCKET_TTL_SECONDS: ${RATE_LIMIT_BUCKET_TTL_SECONDS:-3600}"
                in compose
                and "RATE_LIMIT_PER_MINUTE=120" in env_example
                and "RATE_LIMIT_BURST=240" in env_example
                and "RATE_LIMIT_MAX_BUCKETS=10000" in env_example
                and "RATE_LIMIT_BUCKET_TTL_SECONDS=3600" in env_example
            ),
        },
        {
            "name": "security:shared_state_locking",
            "ok": (
                "GALLERY_LOCK = threading.RLock()" in portrait_gallery_impl
                and "VIDEO_JOBS_LOCK = threading.RLock()" in portrait_jobs
                and "STREAMS_LOCK = threading.RLock()" in portrait_streams
                and "THRESHOLD_PROFILES_LOCK = threading.RLock()" in portrait_thresholds
                and "METRICS_LOCK = threading.RLock()"
                in (root / "app" / "metrics.py").read_text(encoding="utf-8")
                and "BUCKETS_LOCK = threading.RLock()" in rate_limit
                and "def stream_records_snapshot" in portrait_streams
                and "stream_records_snapshot()" in portrait_admin_routes
                and "stream_records_snapshot()" in portrait_stream_routes
            ),
        },
        {
            "name": "security:global_request_body_limit",
            "ok": (
                "MAX_REQUEST_BODY_BYTES" in settings
                and "def limit_request_body" in server
                and 'request.headers.get("content-length")' in server
                and "request.receive" in server
                and "HTTPException(status_code=413" in server
                and "MAX_REQUEST_BODY_BYTES: ${MAX_REQUEST_BODY_BYTES:-117440512}"
                in compose
                and "MAX_REQUEST_BODY_BYTES=117440512" in env_example
                and 'CPU_FALLBACK_ENABLED = parse_bool_env("CPU_FALLBACK_ENABLED", True)'
                in settings
                and "CPU_FALLBACK_ENABLED: ${CPU_FALLBACK_ENABLED:-true}" in compose
                and "CPU_FALLBACK_ENABLED=true" in env_example
                and 'FORCE_CPU = parse_bool_env("FORCE_CPU", False)' in settings
                and "FORCE_CPU=true" in cpu_dockerfile
                and 'FORCE_CPU: "true"' in cpu_compose
                and "FORCE_CPU: ${FORCE_CPU" not in cpu_compose
                and "CPU_TRUSTED_HOSTS" in cpu_compose
                and "cpu-worker-0" in cpu_compose
                and "gpu-worker-0" not in cpu_compose
                and "CPU_TRUSTED_HOSTS=127.0.0.1,localhost,cpu-worker-0,portrait-stream-worker"
                in env_example
            ),
        },
    ]
