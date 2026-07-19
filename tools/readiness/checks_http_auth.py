"""HTTP 与认证加固门禁：TrustedHost、compose/env 默认值、JWT/RBAC、最小披露与安全响应头。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_http_auth_hardening(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    settings = src["settings"]
    server = src["server"]
    compose = src["compose"]
    env_example = src["env_example"]
    portrait_auth = src["portrait_auth"]
    portrait_admin_routes = src["portrait_admin_routes"]
    debug_routes = src["debug_routes"]
    health_routes = src["health_routes"]
    portrait_console_routes = src["portrait_console_routes"]
    portrait_access_routes = src["portrait_access_routes"]
    portrait_access = src["portrait_access"]
    portrait_call_logs = src["portrait_call_logs"]
    console_module_sources = src["console_module_sources"]
    rate_limit = src["rate_limit"]
    portrait_errors = src["portrait_errors"]
    portrait_review = src["portrait_review"]
    portrait_review_routes = src["portrait_review_routes"]
    portrait_bootstrap = src["portrait_bootstrap"]
    portrait_response = src["portrait_response"]
    ready_deep_section = src["ready_deep_section"]
    postgres_health_section = src["postgres_health_section"]
    redis_health_section = src["redis_health_section"]
    local_object_health_section = src["local_object_health_section"]
    s3_health_section = src["s3_health_section"]
    portrait_audit = src["portrait_audit"]
    portrait_postgres_impl = src["portrait_postgres_impl"]
    portrait_task_queue = src["portrait_task_queue"]
    portrait_vector_store = src["portrait_vector_store"]
    observability = src["observability"]
    portrait_gallery_impl = src["portrait_gallery_impl"]
    portrait_jobs = src["portrait_jobs"]
    portrait_streams = src["portrait_streams"]
    runtime_execution = src["runtime_execution"]
    runtime_registry = src["runtime_registry"]
    video_io = src["video_io"]
    media_video_decode = src["media_video_decode"]
    model_query_routes = src["model_query_routes"]
    portrait_model_routes = src["portrait_model_routes"]
    rollout_routes = src["rollout_routes"]
    model_config_writer = src["model_config_writer"]
    model_package = src["model_package"]
    vision_routes = src["vision_routes"]
    person_tracks_routes = src["person_tracks_routes"]
    image_io = src["image_io"]
    media_image_decode = src["media_image_decode"]
    portrait_stream_worker = src["portrait_stream_worker"]
    predict_routes = src["predict_routes"]
    routes_inference_common = src["routes_inference_common"]
    rollback_route_text = src["rollback_route_text"]
    security_headers = src["security_headers"]
    security = src["security"]
    requirements = src["requirements"]
    portrait_crypto = src["portrait_crypto"]
    return [
        {
            "name": "security:trusted_host_allowlist",
            "ok": (
                "TRUSTED_HOSTS" in settings
                and "HotReloadTrustedHostMiddleware" in server
                and "allowed_hosts_getter=lambda: TRUSTED_HOSTS" in server
                and "www_redirect=False" in server
                and "TRUSTED_HOSTS: ${TRUSTED_HOSTS:-127.0.0.1,localhost,gpu-worker-0,gpu-worker-1}"
                in compose
                and "TRUSTED_HOSTS=127.0.0.1,localhost,gpu-worker-0,gpu-worker-1"
                in env_example
            ),
        },
        {
            "name": "security:compose_auth_required_default",
            "ok": "AUTH_REQUIRED: ${AUTH_REQUIRED:-true}" in compose,
        },
        {
            "name": "security:compose_debug_disabled_default",
            "ok": "DEBUG_ENDPOINTS_ENABLED: ${DEBUG_ENDPOINTS_ENABLED:-false}"
            in compose,
        },
        {
            "name": "security:env_example_fail_closed_defaults",
            "ok": (
                "AUTH_REQUIRED=true" in env_example
                and "DEBUG_ENDPOINTS_ENABLED=false" in env_example
                and "ENABLE_API_DOCS=false" in env_example
                and "TENANT_HEADER_REQUIRED=true" in env_example
                and "ENCRYPTION_KEY_ID=primary" in env_example
                and "REQUIRE_ENCRYPTION=true" in env_example
                and "AUDIT_WRITE_FAIL_CLOSED=true" in env_example
                and "MODEL_CONFIG_READ_FAIL_CLOSED=true" in env_example
                and "STATE_READ_FAIL_CLOSED=true" in env_example
                and "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES=false"
                in env_example
                and "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES: ${PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES:-false}"
                in compose
            ),
        },
        {
            "name": "security:jwt_claim_contract",
            "ok": (
                "JWT_REQUIRE_EXP" in settings
                and "JWT_REQUIRE_ISS" in settings
                and "JWT_REQUIRE_AUD" in settings
                and "JWT_REQUIRE_TENANT" in settings
                and "JWT_AUDIENCE" in settings
                and "JWT_SECRET_ID" in settings
                and "JWT_SECRET_KEYRING" in settings
                and "def parse_jwt_secret_keyring" in portrait_auth
                and "def candidate_jwt_secrets" in portrait_auth
                and 'header.get("kid")' in portrait_auth
                and "missing JWT expiration" in portrait_auth
                and "missing JWT issuer" in portrait_auth
                and "missing JWT audience" in portrait_auth
                and "invalid JWT audience" in portrait_auth
                and "JWT_AUDIENCE: ${JWT_AUDIENCE:-portrait-hub-api}" in compose
                and "JWT_SECRET_ID: ${JWT_SECRET_ID:-primary}" in compose
                and "JWT_SECRET_KEYRING: ${JWT_SECRET_KEYRING:-}" in compose
                and "JWT_REQUIRE_EXP: ${JWT_REQUIRE_EXP:-true}" in compose
                and "JWT_REQUIRE_ISS: ${JWT_REQUIRE_ISS:-true}" in compose
                and "JWT_REQUIRE_AUD: ${JWT_REQUIRE_AUD:-true}" in compose
                and "JWT_REQUIRE_TENANT: ${JWT_REQUIRE_TENANT:-true}" in compose
                and "JWT_AUDIENCE=portrait-hub-api" in env_example
                and "JWT_SECRET_ID=primary" in env_example
                and "JWT_SECRET_KEYRING=" in env_example
                and "JWT_REQUIRE_EXP=true" in env_example
                and "JWT_REQUIRE_ISS=true" in env_example
                and "JWT_REQUIRE_AUD=true" in env_example
                and "JWT_REQUIRE_TENANT=true" in env_example
                and '"jwt_secret_id_configured": bool(JWT_SECRET_ID)'
                in portrait_admin_routes
                and '"jwt_secret_keyring_configured": bool(JWT_SECRET_KEYRING)'
                in portrait_admin_routes
            ),
        },
        {
            "name": "security:least_privilege_rbac_roles",
            "ok": (
                '"admin": {"*"}' in portrait_auth
                and '"operator": {"infer", "compare", "gallery:read", "gallery:write", "jobs", "streams", "models:read", "admin:status", "metrics:read", "access:read"}'
                in portrait_auth
                and '"algorithm": {"infer", "compare", "models:read", "models:write", "thresholds:write"}'
                in portrait_auth
                and '"auditor": {"gallery:read", "jobs:read", "streams:read", "models:read", "admin:status", "admin:export", "metrics:read", "access:read"}'
                in portrait_auth
                and '"viewer": {"gallery:read", "jobs:read", "streams:read", "models:read"}'
                in portrait_auth
                and '"viewer": {"infer"' not in portrait_auth
                and 'permission_dependency("models:write")' in debug_routes
                and 'permission_dependency("models:read")' not in debug_routes
                and '@router.get("/metrics", dependencies=[Depends(require_api_token), Depends(permission_dependency("metrics:read"))])'
                in health_routes
                and 'permission_dependency("admin:status")' in portrait_admin_routes
                and 'permission_dependency("admin:export")' in portrait_admin_routes
                and 'permission_dependency("admin:retention")' in portrait_admin_routes
                and 'permission_dependency("models:read")' not in portrait_admin_routes
                and 'permission_dependency("models:write")' not in portrait_admin_routes
                and '@router.get("/v1/console/me", dependencies=[Depends(require_api_token)])'
                in portrait_console_routes
                and 'permission_dependency("admin:status")' not in portrait_console_routes
                and 'permission_dependency("access:read")' in portrait_access_routes
                and 'permission_dependency("access:write")' in portrait_access_routes
                and "application_scopes_allow_permission" in portrait_auth
                and "x_api_key" in portrait_auth
            ),
        },
        {
            "name": "security:access_center_state_safety",
            "ok": (
                "_ACCESS_LOCK = threading.RLock()" in portrait_access
                and "with _ACCESS_LOCK:" in portrait_access
                and "def record_application_call" in portrait_access
                and "call_count" in portrait_access
                and "error_count" in portrait_access
                and "last_error_at" in portrait_access
                and "error_rate" in portrait_access
                and "record_application_call(tenant_id, application_id, status_code, created_at)"
                in portrait_call_logs
                and "error_code: str | None = None" in portrait_call_logs
                and "created_since: float | None = None" in portrait_call_logs
                and "created_until: float | None = None" in portrait_call_logs
                and "error_code: str | None = Query" in portrait_access_routes
                and "created_since: float | None = Query" in portrait_access_routes
                and "created_until: float | None = Query" in portrait_access_routes
                and "const errorCode = ref(" in console_module_sources
                and "const createdRange = ref" in console_module_sources
                and 'params.set("error_code", errorCode.value)' in console_module_sources
                and 'params.set("created_since"' in console_module_sources
                and 'params.set("created_until"' in console_module_sources
                and "error_code=logged_error_code" in server
                and "portrait_error_code" in server
                and "portrait_application_id" in rate_limit
                and "portrait_application_id" in server
                and "flush_access_call_stats" in portrait_access
                and "_ACCESS_STATS_DIRTY" in portrait_access
            ),
        },
        {
            "name": "security:access_error_code_catalog",
            "ok": (
                "ERROR_CODE_CATALOG" in portrait_errors
                and "def error_code_catalog" in portrait_errors
                and "validation_error" in portrait_errors
                and "rate_limited" in portrait_errors
                and "storage_error" in portrait_errors
                and "batch_job_error" in portrait_errors
                and "migration_error" in portrait_errors
                and "from app.portrait_errors import error_code_catalog" in portrait_access_routes
                and '@router.get("/v1/access/error-codes", dependencies=[Depends(permission_dependency("access:read"))])'
                in portrait_access_routes
                and '"error_codes": error_codes' in portrait_access_routes
                and "/v1/access/error-codes" in console_module_sources
                and "const errorCatalog = ref" in console_module_sources
                and "const selectedErrorCode = ref" in console_module_sources
                and "selectedError.operator_action" in console_module_sources
                and "item.http_status" in console_module_sources
                and "item.retryable" in console_module_sources
            ),
        },
        {
            "name": "security:track_review_annotation_pool",
            "ok": (
                "_REVIEW_LOCK = threading.RLock()" in portrait_review
                and "PORTRAIT_REVIEW_STATE_PATH" in settings
                and "def create_review_annotation" in portrait_review
                and "def list_review_annotations" in portrait_review
                and "def review_annotation_summary" in portrait_review
                and "def list_review_datasets" in portrait_review
                and "def review_threshold_recommendations" in portrait_review
                and "review_annotation_heuristic" in portrait_review
                and '"auto_apply": False' in portrait_review
                and 'record.get("tenant_id") != tenant_id' in portrait_review
                and "不支持的审阅标签" in portrait_review
                and "restore_review_state" in portrait_review_routes
                and 'permission_dependency("jobs:read")' in portrait_review_routes
                and 'permission_dependency("jobs")' in portrait_review_routes
                and '"track_review_annotation_created"' in portrait_review_routes
                and "load_review_state()" in portrait_bootstrap
                and "/v1/evaluation/datasets" in console_module_sources
                and "/v1/evaluation/threshold-recommendations" in console_module_sources
                and "/v1/evaluation/track-reviews" in console_module_sources
                and "/v1/evaluation/track-reviews/summary" in console_module_sources
                and "const reviewSummary = ref" in console_module_sources
                and "const datasets = ref" in console_module_sources
                and "const recommendationPayload = ref" in console_module_sources
                and 'value="confirmed"' in console_module_sources
                and 'value="mismatch"' in console_module_sources
                and "frame_index" in console_module_sources
                and "evidence_ref" in console_module_sources
            ),
        },
        {
            "name": "security:public_health_minimal_disclosure",
            "ok": (
                '"status": "healthy"' in health_routes
                and '"version": APP_VERSION' in health_routes
                and '"models_root"' not in health_routes
                and '"loaded_models"' not in health_routes
                and '"available_providers": available'
                not in health_routes.split('@router.get("/ready/deep"')[0]
                and 'detail={"status": "not_ready"}' in health_routes
                and 'return {"status": "ready"}' in health_routes
                and "runtime_provider_status(available)" in health_routes
            ),
        },
        {
            "name": "security:diagnostic_health_minimal_disclosure",
            "ok": (
                "HEALTH_CHECK_FAILED" in portrait_response
                and "MODEL_READINESS_CHECK_FAILED" in portrait_response
                and "MODEL_READINESS_CHECK_FAILED" in ready_deep_section
                and '"path": str(model_path)' not in ready_deep_section
                and "str(exc)" not in ready_deep_section
                and "HEALTH_CHECK_FAILED" in postgres_health_section
                and '"error": HEALTH_CHECK_FAILED' in postgres_health_section
                and "str(exc)" not in postgres_health_section
                and "HEALTH_CHECK_FAILED" in redis_health_section
                and '"error": HEALTH_CHECK_FAILED' in redis_health_section
                and "str(exc)" not in redis_health_section
                and '"storage_dir_configured": bool(OBJECT_STORAGE_DIR)'
                in local_object_health_section
                and '"path": str(OBJECT_STORAGE_DIR)' not in local_object_health_section
                and '"bucket_configured": bool(S3_BUCKET)' in s3_health_section
                and '"endpoint_configured": bool(S3_ENDPOINT_URL)' in s3_health_section
                and '"region_configured": bool(S3_REGION)' in s3_health_section
                and '"bucket": S3_BUCKET' not in s3_health_section
                and '"endpoint": S3_ENDPOINT_URL' not in s3_health_section
                and '"region": S3_REGION' not in s3_health_section
            ),
        },
        {
            "name": "security:backend_failure_log_minimal_disclosure",
            "ok": (
                "exception_log_summary(exc)" in portrait_audit
                and portrait_postgres_impl.count("exception_log_summary(exc)") >= 5
                and "exception_log_summary(exc)" in portrait_task_queue
                and portrait_vector_store.count("exception_log_summary(exc)") >= 2
                and 'logger.warning("postgres audit write failed: %s", exc)'
                not in portrait_audit
                and 'logger.warning("postgres 健康检查失败: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres gallery load failed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres threshold load failed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres video job load failed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres stream load failed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("redis task queue 健康检查失败: %s", exc)'
                not in portrait_task_queue
                and 'logger.warning("pgvector 检索失败，回退到本地向量扫描: %s", exc)'
                not in portrait_vector_store
                and 'logger.warning("Qdrant 检索失败，回退到本地向量扫描: %s", exc)'
                not in portrait_vector_store
            ),
        },
        {
            "name": "security:structured_log_context",
            "ok": (
                "class JsonLogFormatter" in observability
                and "ContextVar" in observability
                and "REQUEST_ID_CONTEXT" in observability
                and "TENANT_ID_CONTEXT" in observability
                and "TRACEPARENT_CONTEXT" in observability
                and "def set_log_context" in observability
                and "def reset_log_context" in observability
                and 'payload["request_id"] = request_id' in observability
                and 'payload["tenant_id"] = tenant_id' in observability
                and "context_tokens = set_log_context(" in server
                and "reset_log_context(context_tokens)" in server
            ),
        },
        {
            "name": "security:runtime_failure_log_minimal_disclosure",
            "ok": (
                portrait_gallery_impl.count("exception_log_summary(exc)") >= 3
                and portrait_jobs.count("exception_log_summary(exc)") >= 2
                and portrait_streams.count("exception_log_summary(exc)") >= 2
                and "exception_log_summary(exc)" in health_routes
                and "exception_log_summary(exc)" in runtime_execution
                and "def model_path_fingerprint" in runtime_registry
                and "model_path_fingerprint(model_path)" in runtime_registry
                and "exception_log_summary(exc)" in runtime_registry
                and 'logger.warning("删除临时视频文件失败")' in video_io
                and 'logger.warning("删除临时视频文件失败")' in media_video_decode
                and 'logger.warning("已跳过无效人员库人员状态: %s", exc)'
                not in portrait_gallery_impl
                and 'logger.warning("向量写入失败: %s", exc)'
                not in portrait_gallery_impl
                and 'logger.warning("向量删除失败: %s", exc)'
                not in portrait_gallery_impl
                and 'logger.warning("已跳过无效视频任务状态: %s", exc)'
                not in portrait_jobs
                and 'logger.warning("skipping stream with unreadable protected URL: %s", exc)'
                not in portrait_streams
                and 'logger.warning("已跳过无效视频流状态: %s", exc)'
                not in portrait_streams
                and 'logger.warning("深度就绪模型检查失败 %s: %s", key, exc)'
                not in health_routes
                and 'logger.warning("批量推理失败，回退到逐帧推理: %s", exc)'
                not in runtime_execution
                and 'logger.info("loading model: %s from %s", cache_key_value, model_path)'
                not in runtime_registry
                and 'logger.exception("加载模型失败: %s", cache_key_value)'
                not in runtime_registry
                and 'logger.warning("删除临时视频文件失败: %s", temp_path)'
                not in video_io
                and 'logger.warning("删除临时视频文件失败: %s", temp_path)'
                not in media_video_decode
            ),
        },
        {
            "name": "security:model_management_minimal_disclosure",
            "ok": (
                '"path": bundle["path"]' not in runtime_registry
                and '"artifact_resolved": bool(bundle.get("path"))' in runtime_registry
                and '"config_path": str(MODEL_CONFIG_PATH)' not in model_query_routes
                and '"config_path": str(MODEL_CONFIG_PATH)' not in portrait_model_routes
                and '"config_path": str(MODEL_CONFIG_PATH)' not in rollout_routes
                and '"config_path": str(MODEL_CONFIG_PATH)' not in model_config_writer
                and '"path": str(model_path)' not in model_query_routes
                and "public_model_config" in model_query_routes
                and "public_model_config" in portrait_model_routes
                and '"model_card": artifact.get("model_card")' not in model_package
                and '"labels": artifact.get("labels")' not in model_package
                and '"path_configured": bool(artifact.get("path"))' in model_package
                and 'detail="模型配置文件不存在"' in model_config_writer
                and 'detail="模型构件不存在"' in model_package
                and 'detail=f"模型配置文件不存在: {MODEL_CONFIG_PATH}"'
                not in model_config_writer
                and "detail=f\"model '{model_name}' was not found under project '{project_name}'\""
                not in model_package
            ),
        },
        {
            "name": "security:request_id_normalization",
            "ok": (
                "REQUEST_ID_PATTERN" in observability
                and "def normalize_request_id" in observability
                and 'request.headers.get("x-request-id")' in observability
                and "request.state.request_id" in observability
                and "str(uuid.uuid4())" in observability
                and 'response.headers["X-Request-ID"] = request_id' in server
            ),
        },
        {
            "name": "security:validation_error_redaction",
            "ok": (
                "RequestValidationError" in server
                and "def validation_error_payload" in server
                and "def validation_error_loc" in server
                and 'loc[-1] = "extra_field"' in server
                and "@app.exception_handler(RequestValidationError)" in server
                and '"input"'
                not in server.split("def validation_error_payload", 1)[1].split(
                    "def create_app", 1
                )[0]
                and '"ctx"'
                not in server.split("def validation_error_payload", 1)[1].split(
                    "def create_app", 1
                )[0]
                and '"url"'
                not in server.split("def validation_error_payload", 1)[1].split(
                    "def create_app", 1
                )[0]
                and 'detail="不支持的视觉任务"' in vision_routes
                and "不支持的视觉任务: {task_name}" not in vision_routes
            ),
        },
        {
            "name": "security:upload_validation_error_minimal_disclosure",
            "ok": (
                'detail="上传文件为空"' in image_io
                and 'detail=f"上传文件过大：最大 {MAX_IMAGE_BYTES} 字节"' in image_io
                and 'detail="不支持的图片扩展名"' in media_image_decode
                and 'detail="上传文件包含不支持的图片内容"' in media_image_decode
                and 'detail="不支持的图片格式"' in media_image_decode
                and 'detail="图片内容与解码出的图片格式不匹配"' in media_image_decode
                and 'detail="上传文件不是有效图片"' in media_image_decode
                and 'detail=f"上传文件过大：最大 {max_bytes} 字节"' in media_image_decode
                and 'detail=f"图片像素过多：最大 {MAX_IMAGE_PIXELS}"' in media_image_decode
                and "image filename format mismatch ignored" in media_image_decode
                and 'detail="图片扩展名与检测到的内容不匹配"' not in media_image_decode
                and 'detail="不支持的视频扩展名"' in video_io
                and 'detail="上传视频包含不支持的容器内容"' in video_io
                and 'detail="视频扩展名与检测到的内容不匹配"' in video_io
                and 'detail="上传视频为空"' in video_io
                and 'detail=f"上传视频过大：最大 {MAX_VIDEO_BYTES} 字节"' in video_io
                and "不支持的图片扩展名 '{suffix}'" not in media_image_decode
                and "image extension does not match detected {detected.lower()} content" not in media_image_decode
                and "不支持的图片格式 '{image_format}'" not in media_image_decode
                and "image content sniffed as {detected_format.lower()}" not in media_image_decode
                and "uploaded file is too large: {len(data)} bytes" not in image_io
                and "uploaded file is too large: {len(data)} bytes" not in media_image_decode
                and "图片像素过多: {width * height}" not in media_image_decode
                and "不支持的视频扩展名 '{suffix}'" not in video_io
                and "video extension does not match detected {container} content" not in video_io
                and "uploaded video is too large: {len(data)} bytes" not in video_io
                and "uploaded file '{file.filename}'" not in image_io
                and "uploaded file '{file.filename}'" not in media_image_decode
                and "uploaded file '{filename" not in media_image_decode
                and "image extension for '{filename}'" not in media_image_decode
                and "uploaded video '{file.filename}'" not in video_io
                and "uploaded video '{filename" not in video_io
                and "video extension for '{filename}'" not in video_io
            ),
        },
        {
            "name": "security:biometric_vector_default_minimal_disclosure",
            "ok": (
                "image_fingerprint_embedding" not in portrait_jobs
                and "include_embeddings: bool = Form(False)" in person_tracks_routes
                and "include_embeddings: bool = False" in portrait_jobs
                and '"include_embeddings": _bool_setting(settings, "include_embeddings", False)'
                in portrait_stream_worker
                and "include_vectors: bool = Form(False)" in vision_routes
                and "if include_vectors:" in vision_routes
            ),
        },
        {
            "name": "security:unhandled_error_redaction",
            "ok": (
                "def internal_error_payload" in server
                and '"internal_error"' in server
                and '"request_id": request_id' in server
                and "v1_contract=uses_v1_contract(request)" in server
                and 'response.headers["X-Request-ID"] = request_id' in server
                and "apply_security_headers(response)" in server
                and "raise"
                not in server.split("except Exception:", 1)[1].split(
                    "duration = now() - start", 1
                )[0]
            ),
        },
        {
            "name": "security:runtime_error_response_redaction",
            "ok": (
                "def raise_internal_error" in portrait_response
                and '"message": detail' in portrait_response
                and '"request_id": request_id' in portrait_response
                and "def inference_error_boundary" in routes_inference_common
                and "except HTTPException:" in routes_inference_common
                and "raise_internal_error(request_id, internal_message)"
                in routes_inference_common
                and "internal_message=" in vision_routes
                and "internal_message=" in person_tracks_routes
                and "inference_error_boundary(" in vision_routes
                and "inference_error_boundary(" in person_tracks_routes
                and "raise_internal_error(request_id" in predict_routes
                and "raise_internal_error(request_id" in debug_routes
                and 'detail="加载模型运行时失败"' in runtime_registry
                and "运行时错误: {exc}"
                not in "\n".join(
                    [
                        predict_routes,
                        vision_routes,
                        person_tracks_routes,
                        portrait_jobs,
                        portrait_stream_worker,
                        debug_routes,
                        routes_inference_common,
                    ]
                )
            ),
        },
        {
            "name": "security:runtime_error_log_minimal_disclosure",
            "ok": (
                'logger.warning("%s: request_id=%s error=%s", log_label, request_id, exception_log_summary(exc))'
                in routes_inference_common
                and 'log_label="vision inference failed"' in vision_routes
                and 'log_label="person track inference failed"' in person_tracks_routes
                and "exception_log_summary(exc)" in portrait_jobs
                and "exception_log_summary(error)" in portrait_stream_worker
                and "exception_log_summary(exc)" in predict_routes
                and "exception_log_summary(exc)" in debug_routes
                and "logger.exception("
                not in "\n".join(
                    [
                        predict_routes,
                        vision_routes,
                        person_tracks_routes,
                        portrait_jobs,
                        portrait_stream_worker,
                        debug_routes,
                        routes_inference_common,
                    ]
                )
            ),
        },
        {
            "name": "security:rollback_failure_response_redaction",
            "ok": (
                "def raise_rollback_failure" in portrait_response
                and "def exception_log_summary" in portrait_response
                and "exception_log_summary(original_error)" in portrait_response
                and '"rollback_failed": True' in portrait_response
                and '"rollback_error_count": len(rollback_errors)' in portrait_response
                and '"rollback_errors": rollback_errors' not in portrait_response
                and 'getattr(original_error, "detail", original_error)'
                not in portrait_response
                and '"error": str(getattr(original_error' not in rollback_route_text
                and '"rollback_errors": rollback_errors' not in rollback_route_text
                and rollback_route_text.count("raise_rollback_failure(") >= 5
            ),
        },
        {
            "name": "security:http_security_headers",
            "ok": (
                "SECURITY_HEADERS_ENABLED" in settings
                and "CONTENT_SECURITY_POLICY" in settings
                and "HSTS_ENABLED" in settings
                and "HSTS_MAX_AGE_SECONDS" in settings
                and 'response.headers.setdefault("Content-Security-Policy"'
                in security_headers
                and 'response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")'
                in security_headers
                and 'response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")'
                in security_headers
                and 'response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")'
                in security_headers
                and 'response.headers.setdefault("X-Download-Options", "noopen")'
                in security_headers
                and 'response.headers.setdefault("Strict-Transport-Security", hsts_header_value())'
                in security_headers
                and "CONTENT_SECURITY_POLICY:" in compose
                and "HSTS_ENABLED: ${HSTS_ENABLED:-true}" in compose
                and "HSTS_MAX_AGE_SECONDS: ${HSTS_MAX_AGE_SECONDS:-31536000}" in compose
                and "CONTENT_SECURITY_POLICY=" in env_example
                and "HSTS_ENABLED=true" in env_example
            ),
        },
        {
            "name": "security:http_exception_protocol_headers",
            "ok": (
                "headers=exc.headers" in server
                and ("def unauthorized" in security or "unauthorized" in security)
                and "def unauthorized" in portrait_auth
                and '"WWW-Authenticate": "Bearer"' in portrait_auth
                and "Retry-After" in rate_limit
                and "def retry_after_seconds" in rate_limit
            ),
        },
        {
            "name": "security:sensitive_payload_authenticated_encryption",
            "ok": (
                "cryptography" in requirements
                and "from cryptography.hazmat.primitives.ciphers.aead import AESGCM"
                in portrait_crypto
                and "REQUIRE_ENCRYPTION" in settings
                and "ENCRYPTION_KEY_ID" in settings
                and "ENCRYPTION_KEYRING" in settings
                and "def encryption_required" in portrait_crypto
                and "def current_encryption_key_id" in portrait_crypto
                and "def parse_encryption_keyring" in portrait_crypto
                and "def candidate_decryption_keys" in portrait_crypto
                and (
                    "if encryption_required()" in portrait_crypto
                    or "if encryption_required():" in portrait_crypto
                )
                and "当 REQUIRE_ENCRYPTION=true 时，ENCRYPTION_KEY 为必填项"
                in portrait_crypto
                and 'AES_GCM_ALGORITHM = "aes-256-gcm"' in portrait_crypto
                and "AES_GCM_NONCE_BYTES = 12" in portrait_crypto
                and "os.urandom(AES_GCM_NONCE_BYTES)" in portrait_crypto
                and "AESGCM(key).encrypt(nonce, data, None)" in portrait_crypto
                and "AESGCM(key).decrypt(nonce, data, None)" in portrait_crypto
                and "InvalidTag" in portrait_crypto
                and "加密载荷认证失败" in portrait_crypto
                and "LEGACY_XOR_ALGORITHM" in portrait_crypto
                and '"key_id": key_id' in portrait_crypto
                and "candidate_decryption_keys(key_id, kdf=kdf_name" in portrait_crypto
                and "candidate_decryption_keys(key_id, kdf=LEGACY_SHA256_KDF)"
                in portrait_crypto
                and "ENCRYPTION_KEY_ID: ${ENCRYPTION_KEY_ID:-primary}" in compose
                and "ENCRYPTION_KEYRING: ${ENCRYPTION_KEYRING:-}" in compose
                and "REQUIRE_ENCRYPTION: ${REQUIRE_ENCRYPTION:-true}" in compose
                and "ENCRYPTION_KEY_ID=primary" in env_example
                and "ENCRYPTION_KEYRING=" in env_example
                and "REQUIRE_ENCRYPTION=true" in env_example
                and '"require_encryption": REQUIRE_ENCRYPTION' in portrait_admin_routes
                and '"encryption_key_id_configured": bool(ENCRYPTION_KEY_ID)'
                in portrait_admin_routes
                and '"encryption_keyring_configured": bool(ENCRYPTION_KEYRING)'
                in portrait_admin_routes
                and '"algorithm": AES_GCM_ALGORITHM' in portrait_crypto
                and '"algorithm": LEGACY_XOR_ALGORITHM' not in portrait_crypto
            ),
        },
    ]
