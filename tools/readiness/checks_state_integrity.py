"""状态完整性门禁：状态读写 fail-closed、补偿事务、保留清理与对象存储原子写。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_state_integrity(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    settings = src["settings"]
    portrait_state = src["portrait_state"]
    compose = src["compose"]
    env_example = src["env_example"]
    portrait_gallery_impl = src["portrait_gallery_impl"]
    portrait_jobs = src["portrait_jobs"]
    portrait_streams = src["portrait_streams"]
    portrait_thresholds = src["portrait_thresholds"]
    portrait_task_queue = src["portrait_task_queue"]
    portrait_job_routes = src["portrait_job_routes"]
    portrait_video_job_worker = src["portrait_video_job_worker"]
    portrait_gallery_routes = src["portrait_gallery_routes"]
    portrait_stream_routes = src["portrait_stream_routes"]
    portrait_gallery_mutations = src["portrait_gallery_mutations"]
    portrait_postgres_impl = src["portrait_postgres_impl"]
    portrait_admin_routes = src["portrait_admin_routes"]
    portrait_admin_runtime_text = src["portrait_admin_runtime_text"]
    portrait_object_storage = src["portrait_object_storage"]
    portrait_gallery_mutation_text = src["portrait_gallery_mutation_text"]
    object_delete_sections = src["object_delete_sections"]
    portrait_gallery = src["portrait_gallery"]
    portrait_gallery_records = src["portrait_gallery_records"]
    portrait_gallery_route_orchestration = src["portrait_gallery_route_orchestration"]
    portrait_postgres_schema = src["portrait_postgres_schema"]
    return [
        {
            "name": "security:state_write_fail_closed",
            "ok": (
                "STATE_WRITE_FAIL_CLOSED" in settings
                and "handle_state_write_error" in portrait_state
                and "状态写入失败" in portrait_state
                and "HTTP_503_SERVICE_UNAVAILABLE" in portrait_state
                and "STATE_WRITE_FAIL_CLOSED: ${STATE_WRITE_FAIL_CLOSED:-true}"
                in compose
                and "STATE_WRITE_FAIL_CLOSED=true" in env_example
            ),
        },
        {
            "name": "security:state_read_fail_closed",
            "ok": (
                "STATE_READ_FAIL_CLOSED" in settings
                and 'STATE_READ_FAIL_CLOSED = parse_bool_env("STATE_READ_FAIL_CLOSED", True)'
                in settings
                and "from app.settings import STATE_READ_FAIL_CLOSED, STATE_WRITE_FAIL_CLOSED"
                in portrait_state
                and "if STATE_READ_FAIL_CLOSED:" in portrait_state
                and 'detail="状态读取失败"' in portrait_state
                and "def handle_state_read_error" in portrait_state
                and "gallery state 根节点必须是映射" in portrait_gallery_impl
                and "video jobs state 根节点必须是映射" in portrait_jobs
                and "streams state 根节点必须是映射" in portrait_streams
                and "threshold state 根节点必须是映射" in portrait_thresholds
                and "STATE_READ_FAIL_CLOSED: ${STATE_READ_FAIL_CLOSED:-true}" in compose
                and "STATE_READ_FAIL_CLOSED=true" in env_example
            ),
        },
        {
            "name": "security:state_file_log_minimal_disclosure",
            "ok": (
                "def state_path_fingerprint" in portrait_state
                and 'hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]'
                in portrait_state
                and "exception_log_summary(exc)" in portrait_state
                and "exception_log_summary(replace_exc)" in portrait_state
                and "path_hash=%s" in portrait_state
                and "读取状态文件失败 %s: %s" not in portrait_state
                and "写入状态文件失败 %s: %s" not in portrait_state
                and "atomic state 替换失败 for %s: %s" not in portrait_state
                and "追加审计文件失败 %s: %s" not in portrait_state
            ),
        },
        {
            "name": "security:job_stream_state_fail_closed",
            "ok": (
                "PORTRAIT_JOBS_STATE_PATH" in settings
                and "PORTRAIT_STREAMS_STATE_PATH" in settings
                and "read_json_state(PORTRAIT_JOBS_STATE_PATH" in portrait_jobs
                and "write_json_state(" in portrait_jobs
                and "PORTRAIT_JOBS_STATE_PATH," in portrait_jobs
                and "read_json_state(PORTRAIT_STREAMS_STATE_PATH" in portrait_streams
                and "write_json_state(PORTRAIT_STREAMS_STATE_PATH" in portrait_streams
                and "restore_video_job(job, previous_job)" in portrait_jobs
                and "restore_stream(stream, previous_stream)" in portrait_streams
                and "PORTRAIT_JOBS_STATE_PATH" in compose
                and "PORTRAIT_STREAMS_STATE_PATH" in compose
                and "PORTRAIT_JOBS_STATE_PATH=/workspace/runtime-state/portrait-jobs.json"
                in env_example
                and "PORTRAIT_STREAMS_STATE_PATH=/workspace/runtime-state/portrait-streams.json"
                in env_example
            ),
        },
        {
            "name": "security:task_queue_enqueue_compensation",
            "ok": (
                "def append_task_queue_state" in portrait_task_queue
                and "fail_closed=required" in portrait_task_queue
                and "TASK_MESSAGE_STORE.remove(message)" in portrait_task_queue
                and (
                    "remove_video_job(job.job_id, tenant_id)" in portrait_job_routes
                    or "run_blocking_io(remove_video_job, job.job_id, tenant_id)"
                    in portrait_job_routes
                )
                and "TASK_QUEUE.enqueue" in portrait_job_routes
                and '"input_ref": input_ref' in portrait_job_routes
                and "TASK_QUEUE.remove" in portrait_job_routes
                and (
                    'audit_event("video_job_created"' in portrait_job_routes
                    or '"video_job_created"' in portrait_job_routes
                )
            ),
        },
        {
            "name": "security:job_audit_compensation",
            "ok": (
                "def rollback_video_job_snapshot" in portrait_job_routes
                and (
                    "remove_video_job(job.job_id, tenant_id)" in portrait_job_routes
                    or "run_blocking_io(remove_video_job, job.job_id, tenant_id)"
                    in portrait_job_routes
                )
                and "restore_video_job(job, previous_job)" in portrait_job_routes
                and (
                    "restore_video_job_in_store(job)" in portrait_job_routes
                    or "persist_video_job(job)" in portrait_job_routes
                )
                and "视频任务变更失败，且回滚持久化失败" in portrait_job_routes
            ),
        },
        {
            "name": "security:tenant_scoped_background_jobs",
            "ok": (
                '"tenant_id": tenant_id' in portrait_job_routes
                and '"input_ref": input_ref' in portrait_job_routes
                and "stage_video_upload" in portrait_job_routes
                and "background_tasks.add_task(" not in portrait_job_routes
                and "portrait-video-job-worker:" in compose
                and "class RedisTaskQueue" in portrait_task_queue
                and "xreadgroup" in portrait_task_queue
                and "xautoclaim" in portrait_task_queue
                and "async def process_video_job_message" in portrait_video_job_worker
                and "validate_video_job_message" in portrait_video_job_worker
                and "input_ref=task[" in portrait_video_job_worker
                and "job = get_video_job(job_id, tenant_id=tenant_id)" in portrait_jobs
                and "tenant_hash=%s job_hash=%s attempt=%s error=%s" in portrait_jobs
            ),
        },
        {
            "name": "security:video_job_error_redaction",
            "ok": (
                'VIDEO_JOB_ERROR_MESSAGE = "视频任务失败"' in portrait_jobs
                and "def public_video_job_error" in portrait_jobs
                and '"error": public_video_job_error(self.error)' in portrait_jobs
                and 'error=public_video_job_error(payload.get("error"))'
                in portrait_jobs
                and "job.error = VIDEO_JOB_ERROR_MESSAGE" in portrait_jobs
                and "exception_log_summary(exc)" in portrait_jobs
                and "video_job_identifier_fingerprint(tenant_id)" in portrait_jobs
                and 'logger.exception("视频任务失败' not in portrait_jobs
                and "job.error = str(exc)" not in portrait_jobs
                and 'detail="任务不存在"' in portrait_job_routes
                and 'detail=f"任务不存在: {job_id}"' not in portrait_job_routes
            ),
        },
        {
            "name": "security:resource_not_found_minimal_disclosure",
            "ok": (
                'detail="人员不存在"' in portrait_gallery_impl
                and 'detail="人员不存在"' in portrait_gallery_routes
                and 'detail="视频流不存在"' in portrait_stream_routes
                and 'detail="任务不存在"' in portrait_job_routes
                and 'detail=f"人员不存在: {resolved_id}"' not in portrait_gallery_impl
                and 'detail=f"人员不存在: {person_id}"' not in portrait_gallery_routes
                and 'detail=f"视频流不存在: {stream_id}"' not in portrait_stream_routes
                and 'detail=f"任务不存在: {job_id}"' not in portrait_job_routes
            ),
        },
        {
            "name": "security:state_mutation_rollback",
            "ok": (
                "previous_person = deepcopy(person)" in portrait_gallery_impl
                and "GALLERY.pop(key, None)" in portrait_gallery_impl
                and "GALLERY[key] = previous_person" in portrait_gallery_impl
                and "person.features = previous_person.features"
                in portrait_gallery_impl
                and "previous_thresholds = threshold_snapshot()" in portrait_thresholds
                and "THRESHOLD_PROFILES.clear()" in portrait_thresholds
                and "THRESHOLD_PROFILES.update(deepcopy(previous_thresholds))"
                in portrait_thresholds
            ),
        },
        {
            "name": "security:gallery_audit_compensation",
            "ok": (
                "def rollback_gallery_mutation" in portrait_gallery_mutations
                and "def restore_gallery_person_snapshot" in portrait_gallery_mutations
                and "created_object_infos: list[dict[str, Any]]"
                in portrait_gallery_mutations
                and "cleanup_object_after_failed_feature(object_info, object_store=object_store)"
                in portrait_gallery_mutations
                and "persist_delete_hook(tenant_id, person_id)"
                in portrait_gallery_mutations
                and 'errors.append("恢复前删除已变更人员失败")'
                in portrait_gallery_mutations
                and "persist_person_hook(restored_person)" in portrait_gallery_mutations
                and "persist_feature_hook(restored_person, feature)"
                in portrait_gallery_mutations
                and "人员库变更失败，且回滚持久化失败" in portrait_gallery_mutations
            ),
        },
        {
            "name": "security:stream_audit_compensation",
            "ok": (
                "def rollback_stream_snapshot" in portrait_stream_routes
                and (
                    "remove_stream(stream.stream_id, tenant_id)"
                    in portrait_stream_routes
                    or "run_blocking_io(remove_stream, stream.stream_id, tenant_id)"
                    in portrait_stream_routes
                )
                and "restore_stream(stream, previous_stream)" in portrait_stream_routes
                and "persist_stream(stream)" in portrait_stream_routes
                and "视频流变更失败，且回滚持久化失败" in portrait_stream_routes
                and "def remove_stream" in portrait_streams
                and "def delete_stream_state" in portrait_streams
            ),
        },
        {
            "name": "security:postgres_stream_event_snapshot_sync",
            "ok": (
                "DELETE FROM portrait_stream_events WHERE tenant_id = %s AND stream_id = %s"
                in portrait_postgres_impl
                and "def delete_stream" in portrait_postgres_impl
                and "DELETE FROM portrait_streams WHERE tenant_id = %s AND stream_id = %s"
                in portrait_postgres_impl
            ),
        },
        {
            "name": "security:retention_cleanup_compensation",
            "ok": (
                "def rollback_retention_cleanup" in portrait_admin_routes
                and "removed_job_snapshots: list[VideoJob]" in portrait_admin_routes
                and "trimmed_stream_snapshots: list[tuple[StreamRecord, StreamRecord]]"
                in portrait_admin_routes
                and "removed_gallery_snapshots: list[PersonRecord]"
                in portrait_admin_routes
                and "def cleanup_retained_gallery_feature_objects"
                in portrait_admin_routes
                and "delete_gallery_person(previous_person.person_id, tenant_id=tenant_id)"
                in portrait_admin_routes
                and "feature_object_infos(person)" in portrait_admin_routes
                and "candidate_gallery_object_reference_count" in portrait_admin_routes
                and "removed_gallery_people" in portrait_admin_routes
                and "deleted_gallery_objects" in portrait_admin_routes
                and "restore_stream(stream, previous_stream)" in portrait_admin_routes
                and (
                    "restore_gallery_person(person)" in portrait_admin_routes
                    or "GALLERY[gallery_key(restored_person.tenant_id, restored_person.person_id)]"
                    in portrait_admin_routes
                )
                and "persist_feature(restored_person, feature)"
                in portrait_admin_runtime_text
                and (
                    "restore_video_job_in_store(job)" in portrait_admin_routes
                    or "persist_video_job(restored_job)" in portrait_admin_routes
                )
                and "persist_stream(stream)" in portrait_admin_routes
                and "OBJECT_CLEANUP_FAILED" in portrait_admin_routes
                and "保留清理失败，且回滚持久化失败" in portrait_admin_routes
            ),
        },
        {
            "name": "security:object_write_compensation",
            "ok": (
                "def delete_object" in portrait_object_storage
                and "OBJECT_DELETE_FAILED" in portrait_object_storage
                and "OBJECT_CLEANUP_FAILED" in portrait_gallery_mutation_text
                and "def object_key_fingerprint" in portrait_object_storage
                and "target.unlink(missing_ok=True)" in portrait_object_storage
                and "delete_object(Bucket=S3_BUCKET, Key=object_key)"
                in portrait_object_storage
                and object_delete_sections.count("exception_log_summary(exc)") >= 2
                and '"object_key": object_key' not in object_delete_sections
                and '"bucket": S3_BUCKET' not in object_delete_sections
                and '"error": str(exc)' not in object_delete_sections
                and "return str(exc)" not in portrait_gallery_mutation_text
                and "def cleanup_object_after_failed_feature"
                in portrait_gallery_mutations
                and "cleanup_object_after_failed_feature(object_info, object_store=object_store)"
                in portrait_gallery_mutations
            ),
        },
        {
            "name": "security:gallery_delete_object_cleanup",
            "ok": (
                "object_info: dict[str, Any] | None = None"
                in (portrait_gallery + portrait_gallery_records)
                and "def feature_object_infos"
                in (portrait_gallery + portrait_gallery_records)
                and 'payload["object_info"] = deepcopy(self.object_info)'
                in (portrait_gallery + portrait_gallery_records)
                and "object_info=deepcopy(object_info) if object_info else None"
                in (portrait_gallery + portrait_gallery_records)
                and "object_info=object_info" in portrait_gallery_route_orchestration
                and "def cleanup_gallery_feature_objects" in portrait_gallery_mutations
                and '"gallery_delete_person_requested"' in portrait_gallery_routes
                and 'outcome="started"' in portrait_gallery_routes
                and "object_reference_count=len(feature_object_infos(previous_person))"
                in portrait_gallery_routes
                and "cleanup_gallery_feature_objects," in portrait_gallery_routes
                and "object_store=OBJECT_STORE" in portrait_gallery_routes
                and "restore_gallery_person_snapshot," in portrait_gallery_routes
                and "persist_delete_hook=persist_person_delete"
                in portrait_gallery_routes
                and "persist_person_hook=persist_person" in portrait_gallery_routes
                and "persist_feature_hook=persist_feature" in portrait_gallery_routes
                and "OBJECT_CLEANUP_FAILED" in portrait_gallery_mutation_text
                and "deleted_object_count" in portrait_gallery_routes
                and "object_info JSONB NOT NULL DEFAULT '{}'::jsonb"
                in portrait_postgres_schema
                and "f.object_info" in portrait_postgres_impl
                and "object_info = EXCLUDED.object_info" in portrait_postgres_impl
                and 'jsonb(feature.get("object_info") if isinstance(feature.get("object_info"), dict) else {})'
                in portrait_postgres_impl
            ),
        },
        {
            "name": "security:local_object_atomic_write",
            "ok": (
                "def write_local_object_payload" in portrait_object_storage
                and "temp_path = target.with_name" in portrait_object_storage
                and "os.replace(temp_path, target)" in portrait_object_storage
                and "except OSError:" in portrait_object_storage
                and "dump(target)" in portrait_object_storage
                and "temp_path.unlink(missing_ok=True)" in portrait_object_storage
                and "write_local_object_payload(target, payload)"
                in portrait_object_storage
            ),
        },
    ]
