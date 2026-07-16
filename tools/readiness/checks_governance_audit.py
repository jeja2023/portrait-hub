"""模型治理与审计门禁：管理面 RBAC、rollout 审计回读、审计链与备份快照回读。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_model_governance_audit(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    rollout_routes = src["rollout_routes"]
    model_lifecycle_routes = src["model_lifecycle_routes"]
    model_query_routes = src["model_query_routes"]
    portrait_model_routes = src["portrait_model_routes"]
    debug_routes = src["debug_routes"]
    vision_routes = src["vision_routes"]
    person_tracks_routes = src["person_tracks_routes"]
    portrait_jobs = src["portrait_jobs"]
    portrait_stream_worker = src["portrait_stream_worker"]
    health_routes = src["health_routes"]
    predict_routes = src["predict_routes"]
    portrait_job_routes = src["portrait_job_routes"]
    portrait_stream_routes = src["portrait_stream_routes"]
    model_config_writer = src["model_config_writer"]
    rollout_audit = src["rollout_audit"]
    portrait_admin_routes = src["portrait_admin_routes"]
    portrait_audit = src["portrait_audit"]
    portrait_postgres_impl = src["portrait_postgres_impl"]
    settings = src["settings"]
    compose = src["compose"]
    env_example = src["env_example"]
    console_module_sources = src["console_module_sources"]
    return [
        {
            "name": "security:legacy_model_management_rbac",
            "ok": (
                'permission_dependency("models:read")' in rollout_routes
                and 'permission_dependency("models:write")' in rollout_routes
                and model_lifecycle_routes.count(
                    'permission_dependency("models:write")'
                )
                >= 2
                and 'permission_dependency("models:write")' in model_query_routes
                and 'permission_dependency("models:read")' in portrait_model_routes
                and 'permission_dependency("models:write")' in portrait_model_routes
                and 'permission_dependency("models:write")' in debug_routes
                and 'permission_dependency("models:read")' not in debug_routes
            ),
        },
        {
            "name": "security:rollout_preview_validation_4xx",
            "ok": (
                "async def rollout_alias_preview" in rollout_routes
                and "validate_alias_name(alias_name)"
                in rollout_routes.split("async def rollout_alias_preview", 1)[1].split(
                    "@router.post", 1
                )[0]
                and "detail=str(exc)"
                not in rollout_routes.split("async def rollout_alias_preview", 1)[
                    1
                ].split("@router.post", 1)[0]
                and 'detail="别名不存在"' in rollout_routes
                and 'detail=f"别名不存在: {alias_name}"' not in rollout_routes
            ),
        },
        {
            "name": "security:legacy_model_reference_error_redaction",
            "ok": (
                "resolve_model_reference(" in vision_routes
                and "validate_model_reference_parts(" in person_tracks_routes
                and "validate_model_reference_parts(" in portrait_jobs
                and "validate_model_reference_parts(" in portrait_stream_worker
                and "resolve_model_reference(" in portrait_model_routes
                and "validate_model_reference_parts(" in debug_routes
                and "detail=str(exc)"
                not in "\n".join(
                    [
                        vision_routes,
                        person_tracks_routes,
                        portrait_jobs,
                        portrait_stream_worker,
                        portrait_model_routes,
                        debug_routes,
                    ]
                )
            ),
        },
        {
            "name": "security:legacy_inference_rbac",
            "ok": (
                'permission_dependency("models:read")' in health_routes
                and 'permission_dependency("infer")' in predict_routes
                and 'permission_dependency("infer")' in person_tracks_routes
                and 'permission_dependency("jobs")' in portrait_job_routes
                and 'permission_dependency("streams")' in portrait_stream_routes
                and 'permission_dependency("infer")' in vision_routes
            ),
        },
        {
            "name": "security:rollout_audit_rollback",
            "ok": (
                "def commit_model_config_with_audit" in model_config_writer
                and "write_raw_model_config(raw)" in model_config_writer
                and "write_rollout_audit(event, result)" in model_config_writer
                and "write_raw_model_config(previous_raw)" in model_config_writer
                and "写入发布审计失败；模型配置已回滚" in model_config_writer
                and '"rolled_back": True' in model_config_writer
                and '"rollback_failed": True' in model_config_writer
                and '"audit_error"' not in model_config_writer
                and '"rollback_error"' not in model_config_writer
                and "os.replace(temp_path, MODEL_CONFIG_PATH)" in model_config_writer
                and "except OSError:" in model_config_writer
            ),
        },
        {
            "name": "security:rollout_audit_readback",
            "ok": (
                "def read_rollout_audit" in rollout_audit
                and "public_rollout_audit_record" in rollout_audit
                and "MAX_ROLLOUT_AUDIT_LIMIT" in rollout_audit
                and "ROLLOUT_AUDIT_FIELDS" in rollout_audit
                and "ROLLOUT_TARGET_FIELDS" in rollout_audit
                and "malformed_count" in rollout_audit
                and "rollout_audit_entries" in rollout_routes
                and 'permission_dependency("models:read")' in rollout_routes
                and "read_rollout_audit(limit)" in rollout_routes
            ),
        },
        {
            "name": "security:management_mutation_audit",
            "ok": (
                '"model_warmup"' in model_lifecycle_routes
                and '"model_reload"' in model_lifecycle_routes
                and '"model_config_reloaded"' in model_query_routes
                and '"model_loaded"' in portrait_model_routes
                and '"model_unloaded"' in portrait_model_routes
                and '"admin_export"' in portrait_admin_routes
                and "stream_events_count=sum" in portrait_admin_routes
                and '"retention_cleanup"' in portrait_admin_routes
            ),
        },
        {
            "name": "security:model_management_audit_compensation",
            "ok": (
                "def model_registry_snapshot" in portrait_model_routes
                and "def restore_model_registry_snapshot" in portrait_model_routes
                and "def model_load_locks_snapshot" in portrait_model_routes
                and "previous_registry = model_registry_snapshot()"
                in portrait_model_routes
                and "previous_locks = model_load_locks_snapshot()"
                in portrait_model_routes
                and "restore_model_registry_snapshot(previous_registry, previous_locks)"
                in portrait_model_routes
                and "previous_thresholds = threshold_snapshot()"
                in portrait_model_routes
                and "def restore_threshold_snapshot" in portrait_model_routes
                and "save_threshold_state()" in portrait_model_routes
                and "模型管理变更失败，且回滚持久化失败" in portrait_model_routes
                and "def model_registry_snapshot" in model_lifecycle_routes
                and "def restore_model_registry_snapshot" in model_lifecycle_routes
                and "def model_load_locks_snapshot" in model_lifecycle_routes
                and "previous_registry = model_registry_snapshot()"
                in model_lifecycle_routes
                and "previous_locks = model_load_locks_snapshot()"
                in model_lifecycle_routes
                and "restore_model_registry_snapshot(previous_registry, previous_locks)"
                in model_lifecycle_routes
            ),
        },
        {
            "name": "security:model_config_reload_audit_compensation",
            "ok": (
                "previous_configs = deepcopy(MODEL_CONFIGS)" in model_query_routes
                and "previous_aliases = deepcopy(MODEL_ALIASES)" in model_query_routes
                and "MODEL_CONFIGS.update(previous_configs)" in model_query_routes
                and "MODEL_ALIASES.update(previous_aliases)" in model_query_routes
                and '"model_config_reloaded"' in model_query_routes
            ),
        },
        {
            "name": "security:audit_payload_limits",
            "ok": (
                "def build_audit_payload" in portrait_audit
                and "def sanitize_audit_value" in portrait_audit
                and "AUDIT_CHAIN_FIELDS" in portrait_audit
                and "AUDIT_HASH_ALGORITHM" in portrait_audit
                and "def audit_payload_hash" in portrait_audit
                and "def seal_audit_payload" in portrait_audit
                and "def last_audit_hash" in portrait_audit
                and "payload = seal_audit_payload(payload, audit_chain_previous_hash())"
                in portrait_audit
                and '"audit_prev_hash"' in portrait_audit
                and '"audit_hash"' in portrait_audit
                and "audit_hash TEXT NOT NULL"
                in (root / "tools" / "portrait_postgres_schema.sql").read_text(
                    encoding="utf-8"
                )
                and "audit_prev_hash TEXT"
                in (root / "tools" / "portrait_postgres_schema.sql").read_text(
                    encoding="utf-8"
                )
                and 'payload.get("audit_hash")' in portrait_postgres_impl
                and "AUDIT_WRITE_FAIL_CLOSED" in settings
                and "fail_closed=AUDIT_WRITE_FAIL_CLOSED" in portrait_audit
                and "if AUDIT_WRITE_FAIL_CLOSED:" in portrait_audit
                and "AUDIT_WRITE_FAIL_CLOSED: ${AUDIT_WRITE_FAIL_CLOSED:-true}"
                in compose
                and "AUDIT_WRITE_FAIL_CLOSED=true" in env_example
                and '"audit_write_fail_closed": AUDIT_WRITE_FAIL_CLOSED'
                in portrait_admin_routes
                and "MAX_AUDIT_PAYLOAD_BYTES" in settings
                and "MAX_AUDIT_DEPTH" in settings
                and "MAX_AUDIT_KEYS" in settings
                and "MAX_AUDIT_LIST_ITEMS" in settings
                and "MAX_AUDIT_STRING_LENGTH" in settings
                and "MAX_AUDIT_PAYLOAD_BYTES" in portrait_audit
                and "MAX_AUDIT_LIST_ITEMS" in portrait_audit
                and "is_sensitive_field(raw_key_text)" in portrait_audit
                and "RESERVED_AUDIT_FIELDS" in portrait_audit
                and 'key = f"field_{key}"' in portrait_audit
                and "audit_omitted_fields" in portrait_audit
                and "audit_omitted_items" in portrait_audit
                and "MAX_AUDIT_PAYLOAD_BYTES: ${MAX_AUDIT_PAYLOAD_BYTES:-32768}"
                in compose
                and "MAX_AUDIT_DEPTH: ${MAX_AUDIT_DEPTH:-6}" in compose
                and "MAX_AUDIT_KEYS: ${MAX_AUDIT_KEYS:-128}" in compose
                and "MAX_AUDIT_LIST_ITEMS: ${MAX_AUDIT_LIST_ITEMS:-64}" in compose
                and "MAX_AUDIT_STRING_LENGTH: ${MAX_AUDIT_STRING_LENGTH:-2048}"
                in compose
                and "MAX_AUDIT_PAYLOAD_BYTES=32768" in env_example
                and "MAX_AUDIT_DEPTH=6" in env_example
                and "MAX_AUDIT_KEYS=128" in env_example
                and "MAX_AUDIT_LIST_ITEMS=64" in env_example
                and "MAX_AUDIT_STRING_LENGTH=2048" in env_example
            ),
        },
        {
            "name": "security:audit_chain_console_verification",
            "ok": (
                "def public_audit_chain_verification" in portrait_audit
                and "state_path_fingerprint(audit_path)" in portrait_audit
                and "verify_audit_chain(audit_path)" in portrait_audit
                and '"/v1/admin/audit/verify"' in portrait_admin_routes
                and "public_audit_chain_verification" in portrait_admin_routes
                and 'permission_dependency("admin:status")' in portrait_admin_routes
                and '"audit_chain": audit_chain' in portrait_admin_routes
                and "/v1/admin/audit/verify" in console_module_sources
                and "auditVerificationPayload" in console_module_sources
                and "auditChainErrorCount" in console_module_sources
                and "audit_chain" in console_module_sources
                and "path_hash" in console_module_sources
            ),
        },
        {
            "name": "security:audit_event_readback",
            "ok": (
                "def read_public_audit_events" in portrait_audit
                and "def audit_event_matches_filters" in portrait_audit
                and "def audit_event_category" in portrait_audit
                and "category_counts" in portrait_audit
                and "outcome_counts" in portrait_audit
                and "def public_audit_event_record" in portrait_audit
                and "PUBLIC_AUDIT_EVENT_FIELDS" in portrait_audit
                and "MAX_PUBLIC_AUDIT_EVENT_LIMIT" in portrait_audit
                and "created_since" in portrait_audit
                and "created_until" in portrait_audit
                and 'payload.get("tenant_id") != tenant_id' in portrait_audit
                and '"api_key"'
                not in portrait_audit.split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[1].split(
                    "def", 1
                )[0]
                and '"/v1/admin/audit/events"' in portrait_admin_routes
                and "read_public_audit_events" in portrait_admin_routes
                and "created_until 必须大于等于 created_since" in portrait_admin_routes
                and "event: str | None = Query" in portrait_admin_routes
                and "outcome: str | None = Query" in portrait_admin_routes
                and "category: str | None = Query" in portrait_admin_routes
                and "不支持的审计事件类别" in portrait_admin_routes
                and 'permission_dependency("admin:status")' in portrait_admin_routes
                and "MAX_PUBLIC_AUDIT_EVENT_LIMIT" in portrait_admin_routes
                and "/v1/admin/audit/events?${auditEventQueryParams().toString()}"
                in console_module_sources
                and "function auditEventQueryParams" in console_module_sources
                and "audit-event-filter-button" in console_module_sources
                and "audit-category-filter-input" in console_module_sources
                and 'params.set("category", categoryFilter)' in console_module_sources
                and "audit-event-table" in console_module_sources
                and "function renderAuditEventRows" in console_module_sources
                and "auditEventsPayload" in console_module_sources
                and "audit_events" in console_module_sources
            ),
        },
        {
            "name": "security:backup_snapshot_readback",
            "ok": (
                "def public_backup_snapshot_record" in portrait_audit
                and "def read_public_backup_snapshots" in portrait_audit
                and "PUBLIC_BACKUP_SNAPSHOT_FIELDS" in portrait_audit
                and "MAX_PUBLIC_BACKUP_SNAPSHOT_LIMIT" in portrait_audit
                and 'record["snapshot_id"] = audit_hash' in portrait_audit
                and 'payload.get("tenant_id") != tenant_id or payload.get("event") != "admin_backup"'
                in portrait_audit
                and '"object_key"'
                not in portrait_audit.split("PUBLIC_BACKUP_SNAPSHOT_FIELDS", 1)[
                    1
                ].split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[0]
                and '"bucket"'
                not in portrait_audit.split("PUBLIC_BACKUP_SNAPSHOT_FIELDS", 1)[
                    1
                ].split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[0]
                and '"sha256"'
                not in portrait_audit.split("PUBLIC_BACKUP_SNAPSHOT_FIELDS", 1)[
                    1
                ].split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[0]
                and '"/v1/admin/backups"' in portrait_admin_routes
                and "read_public_backup_snapshots" in portrait_admin_routes
                and 'permission_dependency("admin:export")' in portrait_admin_routes
                and "MAX_PUBLIC_BACKUP_SNAPSHOT_LIMIT" in portrait_admin_routes
                and "/v1/admin/backups?limit=20" in console_module_sources
                and "backup-snapshot-summary" in console_module_sources
                and "backup-snapshot-table" in console_module_sources
                and "backup-snapshot-refresh-button" in console_module_sources
                and "function renderBackupSnapshots" in console_module_sources
                and "async function refreshAdminData" in console_module_sources
                and "backup_snapshots" in console_module_sources
            ),
        },
    ]
