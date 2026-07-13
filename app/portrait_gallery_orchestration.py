from __future__ import annotations

import asyncio
from io import BytesIO
import json
from collections.abc import Callable
from typing import Any

from fastapi import BackgroundTasks, HTTPException, UploadFile, status
from starlette.datastructures import Headers

from app.media.image_decode import decode_upload_image, decode_upload_images, read_limited_upload
from app.portrait_async import gather_limited, run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_gallery import (
    GALLERY,
    FeatureRecord,
    PersonRecord,
    add_feature,
    persist_feature,
    persist_person,
    persist_person_delete,
    search_gallery,
    upsert_person,
)
from app.portrait_gallery_mutations import rollback_gallery_mutation
from app.portrait_jobs import VideoJob, create_batch_job, image_thumbnail_data_url, persist_video_job, run_batch_job
from app.portrait_model_runtime import (
    embedding_model_info,
    infer_appearance_record_for_image,
    infer_best_face_embedding_for_image,
    infer_body_record_for_image,
)
from app.portrait_object_storage import OBJECT_STORE, public_object_info
from app.portrait_runtime_store import gallery_person_snapshot
from app.portrait_security import normalize_public_metadata
from app.portrait_storage import store_backend_name
from app.portrait_thresholds import normalize_modality, validate_threshold_profile
from app.settings import MAX_EMBEDDING_IMAGES, MAX_GALLERY_SEARCH_BATCH_CONCURRENCY


SUPPORTED_GALLERY_MODALITIES = {"face", "body", "appearance"}


AuditHook = Callable[..., None]
AddFeatureHook = Callable[..., FeatureRecord]
PersistPersonHook = Callable[[PersonRecord], None]
PersistFeatureHook = Callable[[PersonRecord, FeatureRecord], None]
PersistDeleteHook = Callable[[str, str], None]


def parse_gallery_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="元数据必须是有效 JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="元数据必须是 JSON 对象")
    return normalize_public_metadata(parsed, field_name="metadata")


def validate_gallery_modality(modality: str) -> str:
    modality_key = normalize_modality(str(modality))
    if modality_key not in SUPPORTED_GALLERY_MODALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的模态")
    return modality_key


def validate_gallery_image_count(files: list[UploadFile]) -> None:
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要一个图片文件")
    if len(files) > MAX_EMBEDDING_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"图片文件过多：{len(files)}，最大 {MAX_EMBEDDING_IMAGES}",
        )


def combined_query_quality(frame_quality: dict[str, Any] | None, subject_quality: float) -> float:
    frame_score = float(frame_quality.get("score", 0.0)) if isinstance(frame_quality, dict) else 0.0
    return round(subject_quality * 0.76 + frame_score * 0.24, 6)


async def extract_gallery_embedding(image: Any, modality: str) -> tuple[list[float], float, str, str]:
    modality_key = validate_gallery_modality(modality)
    if modality_key == "face":
        embedding, face = await infer_best_face_embedding_for_image(image)
        model_id, model_version = embedding_model_info(face)
        return embedding, float(face["quality"].get("score", 0.0)), model_id, model_version
    if modality_key == "appearance":
        record = await infer_appearance_record_for_image(image, include_embedding=True)
        model_id, model_version = embedding_model_info(record)
        return record["embedding"], float(record["quality"].get("score", 0.0)), model_id, model_version
    record = await infer_body_record_for_image(image, include_embedding=True)
    model_id, model_version = embedding_model_info(record)
    return record["embedding"], float(record["quality"].get("score", 0.0)), model_id, model_version


async def enroll_gallery_person(
    files: list[UploadFile],
    *,
    person_id: str | None,
    display_name: str | None,
    modality: str,
    metadata: str | None,
    request_id: str,
    tenant_id: str,
    object_store: Any = OBJECT_STORE,
    audit_hook: AuditHook = audit_event,
    add_feature_hook: AddFeatureHook = add_feature,
    persist_delete_hook: PersistDeleteHook = persist_person_delete,
    persist_person_hook: PersistPersonHook = persist_person,
    persist_feature_hook: PersistFeatureHook = persist_feature,
) -> dict[str, Any]:
    modality_key = validate_gallery_modality(modality)
    validate_gallery_image_count(files)
    decoded = await decode_upload_images(files)
    previous_person = gallery_person_snapshot(tenant_id, person_id) if person_id is not None else None
    person = await run_blocking_io(
        upsert_person,
        person_id,
        display_name,
        parse_gallery_metadata(metadata),
        tenant_id=tenant_id,
    )
    created = []
    skipped_duplicates = []
    created_object_infos: list[dict[str, Any]] = []
    try:
        for item in decoded:
            if item.frame.duplicate_of is not None:
                skipped_duplicates.append(
                    {
                        "source_id": item.frame.source_id,
                        "duplicate_of": item.frame.duplicate_of,
                        "duplicate_distance": item.frame.duplicate_distance,
                    }
                )
                continue
            object_info = await run_blocking_io(
                object_store.put_bytes,
                tenant_id,
                "gallery-image",
                item.frame.filename,
                item.data or b"",
            )
            thumbnail_url = await run_blocking_io(image_thumbnail_data_url, item.image)
            if thumbnail_url:
                object_info["thumbnail"] = thumbnail_url
            created_object_infos.append(object_info)
            embedding, quality_score, model_id, model_version = await extract_gallery_embedding(item.image, modality_key)
            feature = await run_blocking_io(
                add_feature_hook,
                person,
                modality=modality_key,
                embedding=embedding,
                model_id=model_id,
                model_version=model_version,
                quality_score=quality_score,
                source_id=item.frame.source_id,
                object_info=object_info,
            )
            feature_payload = feature.public_dict()
            feature_payload["object"] = public_object_info(object_info)
            created.append(feature_payload)
        await run_blocking_io(
            audit_hook,
            "gallery_enroll",
            request_id=request_id,
            tenant_id=tenant_id,
            person_id=person.person_id,
            modality=modality_key,
            feature_count=len(created),
            skipped_duplicate_count=len(skipped_duplicates),
        )
    except Exception as exc:
        await run_blocking_io(
            rollback_gallery_mutation,
            tenant_id=tenant_id,
            person_id=person.person_id,
            previous_person=previous_person,
            created_object_infos=created_object_infos,
            original_error=exc,
            object_store=object_store,
            gallery=GALLERY,
            persist_delete_hook=persist_delete_hook,
            persist_person_hook=persist_person_hook,
            persist_feature_hook=persist_feature_hook,
        )
        raise
    return {
        "person": person.public_dict(include_embeddings=False),
        "features": created,
        "feature_count": len(created),
        "input_file_count": len(decoded),
        "skipped_duplicates": skipped_duplicates,
        "skipped_duplicate_count": len(skipped_duplicates),
        "store_backend": store_backend_name(),
    }


async def search_gallery_image(
    file: UploadFile,
    *,
    modality: str,
    top_k: int,
    threshold_profile: str,
    request_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    profile_key = validate_threshold_profile(threshold_profile)
    modality_key = validate_gallery_modality(modality)
    decoded = await decode_upload_image(file)
    embedding, quality_score, _, _ = await extract_gallery_embedding(decoded.image, modality_key)
    frame_quality_score = float(decoded.frame.quality.get("score", 0.0)) if isinstance(decoded.frame.quality, dict) else None
    combined_quality_score = combined_query_quality(decoded.frame.quality, float(quality_score))
    candidates = await run_blocking_io(
        search_gallery,
        embedding,
        modality=modality_key,
        threshold_profile=profile_key,
        top_k=top_k,
        tenant_id=tenant_id,
        query_quality=combined_quality_score,
    )
    retrieval_context = candidates[0].get("retrieval_context", {}) if candidates else {}
    await run_blocking_io(
        audit_event,
        "gallery_search",
        request_id=request_id,
        tenant_id=tenant_id,
        modality=modality_key,
        candidate_count=len(candidates),
    )
    return {
        "candidates": candidates,
        "candidate_count": len(candidates),
        "query": {
            "modality": modality_key,
            "quality_score": round(float(quality_score), 6),
            "frame_quality_score": round(frame_quality_score, 6) if frame_quality_score is not None else None,
            "combined_quality_score": combined_quality_score,
            "threshold_profile": profile_key,
            "top_k": top_k,
            "retrieval_context": retrieval_context,
        },
        "store_backend": store_backend_name(),
    }


async def gallery_search_batch_results(
    files: list[UploadFile],
    *,
    modality: str,
    top_k: int,
    threshold_profile: str,
    tenant_id: str,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
    total = max(1, len(files))
    completed = 0
    progress_lock = asyncio.Lock()

    async def process_file(index: int, file: UploadFile) -> dict[str, Any]:
        nonlocal completed
        decoded = await decode_upload_image(file)
        embedding, quality_score, _, _ = await extract_gallery_embedding(decoded.image, modality)
        combined_quality_score = combined_query_quality(decoded.frame.quality, float(quality_score))
        candidates = await run_blocking_io(
            search_gallery,
            embedding,
            modality=modality,
            threshold_profile=threshold_profile,
            top_k=top_k,
            tenant_id=tenant_id,
            query_quality=combined_quality_score,
        )
        async with progress_lock:
            completed += 1
            if progress_callback is not None:
                await progress_callback(0.05 + 0.9 * (completed / total))
        return {
            "index": index,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "query": {
                "modality": modality,
                "quality_score": round(float(quality_score), 6),
                "combined_quality_score": combined_quality_score,
                "threshold_profile": threshold_profile,
                "top_k": top_k,
            },
        }

    results = await gather_limited(files, process_file, limit=MAX_GALLERY_SEARCH_BATCH_CONCURRENCY)
    results.sort(key=lambda x: x["index"])
    return results


async def search_gallery_batch(
    files: list[UploadFile],
    *,
    modality: str,
    top_k: int,
    threshold_profile: str,
    request_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    validate_gallery_image_count(files)
    profile_key = validate_threshold_profile(threshold_profile)
    modality_key = validate_gallery_modality(modality)
    results = await gallery_search_batch_results(
        files,
        modality=modality_key,
        top_k=top_k,
        threshold_profile=profile_key,
        tenant_id=tenant_id,
    )
    await run_blocking_io(
        audit_event,
        "gallery_search_batch",
        request_id=request_id,
        tenant_id=tenant_id,
        modality=modality_key,
        query_count=len(results),
        candidate_count=sum(item["candidate_count"] for item in results),
    )
    return {
        "results": results,
        "query_count": len(results),
        "store_backend": store_backend_name(),
    }


async def create_async_gallery_search_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile],
    *,
    modality: str,
    top_k: int,
    threshold_profile: str,
    tenant_id: str,
) -> VideoJob:
    validate_gallery_image_count(files)
    profile_key = validate_threshold_profile(threshold_profile)
    modality_key = validate_gallery_modality(modality)
    file_payloads = [(file.filename, file.content_type, await read_limited_upload(file)) for file in files]
    job = await run_blocking_io(
        create_batch_job,
        "gallery_search_batch",
        tenant_id,
        metadata={"query_count": len(file_payloads), "modality": modality_key, "threshold_profile": profile_key},
    )

    async def handler(batch_job: VideoJob) -> dict[str, Any]:
        async def update_progress(progress: float) -> None:
            batch_job.progress = progress
            await run_blocking_io(persist_video_job, batch_job)

        upload_files = [
            UploadFile(filename=name, file=BytesIO(data), headers=Headers({"content-type": ctype or "application/octet-stream"}))
            for name, ctype, data in file_payloads
        ]
        results = await gallery_search_batch_results(
            upload_files,
            modality=modality_key,
            top_k=top_k,
            threshold_profile=profile_key,
            tenant_id=tenant_id,
            progress_callback=update_progress,
        )
        return {
            "results": results,
            "query_count": len(results),
            "store_backend": store_backend_name(),
        }

    background_tasks.add_task(run_batch_job, job.job_id, tenant_id, handler)
    return job


__all__ = [
    "SUPPORTED_GALLERY_MODALITIES",
    "combined_query_quality",
    "create_async_gallery_search_batch",
    "enroll_gallery_person",
    "extract_gallery_embedding",
    "gallery_search_batch_results",
    "parse_gallery_metadata",
    "search_gallery_batch",
    "search_gallery_image",
    "validate_gallery_image_count",
    "validate_gallery_modality",
]
