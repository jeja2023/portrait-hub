from copy import deepcopy
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.media.image_decode import decode_upload_image, decode_upload_images
from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_model_runtime import (
    embedding_model_info,
    infer_appearance_record_for_image,
    infer_best_face_embedding_for_image,
    infer_body_record_for_image,
)
from app.portrait_gallery import (
    GALLERY,
    PersonRecord,
    add_feature,
    delete_person,
    feature_object_infos,
    gallery_key,
    get_person_or_404,
    patch_person,
    persist_feature,
    persist_person,
    persist_person_delete,
    reindex_gallery_vectors,
    search_gallery,
    upsert_person,
)
from app.portrait_jobs import VideoJob, create_batch_job, persist_video_job, run_batch_job
from app.portrait_response import OBJECT_CLEANUP_FAILED, exception_log_summary, portrait_success, raise_rollback_failure
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_request_validation import validate_int_range
from app.portrait_security import normalize_public_metadata
from app.portrait_storage import store_backend_name
from app.portrait_object_storage import OBJECT_STORE, public_object_info
from app.portrait_thresholds import normalize_modality, validate_threshold_profile
from app.security import require_api_token
from app.settings import MAX_EMBEDDING_IMAGES


router = APIRouter(dependencies=[Depends(require_api_token)])


SUPPORTED_GALLERY_MODALITIES = {"face", "body", "appearance"}


class GalleryPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] | None = None


def parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be a JSON object")
    return normalize_public_metadata(parsed, field_name="metadata")


def validate_gallery_modality(modality: str) -> str:
    modality_key = normalize_modality(str(modality))
    if modality_key not in SUPPORTED_GALLERY_MODALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported modality")
    return modality_key


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


async def gallery_search_batch_results(
    files: list[UploadFile],
    *,
    modality: str,
    top_k: int,
    threshold_profile: str,
    tenant_id: str,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
    results = []
    total = max(1, len(files))
    for index, file in enumerate(files):
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
        results.append(
            {
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
        )
        if progress_callback is not None:
            await progress_callback(0.05 + 0.9 * ((index + 1) / total))
    return results


def combined_query_quality(frame_quality: dict[str, Any] | None, subject_quality: float) -> float:
    frame_score = float(frame_quality.get("score", 0.0)) if isinstance(frame_quality, dict) else 0.0
    return round(subject_quality * 0.76 + frame_score * 0.24, 6)


def cleanup_object_after_failed_feature(object_info: dict[str, Any]) -> str | None:
    try:
        result = OBJECT_STORE.delete_object(object_info)
        if not result.get("deleted"):
            logger.warning(
                "object cleanup after feature persistence failure did not delete object: backend=%s reason=%s",
                result.get("backend"),
                result.get("reason"),
            )
            return OBJECT_CLEANUP_FAILED
    except Exception as exc:
        logger.warning("failed to cleanup object after feature persistence failure: %s", exception_log_summary(exc))
        return OBJECT_CLEANUP_FAILED
    return None


def cleanup_gallery_feature_objects(person: PersonRecord) -> tuple[int, list[str]]:
    deleted_count = 0
    errors: list[str] = []
    for object_info in feature_object_infos(person):
        try:
            result = OBJECT_STORE.delete_object(object_info)
            if result.get("deleted"):
                deleted_count += 1
                continue
            logger.warning(
                "object cleanup during gallery person deletion did not delete object: backend=%s reason=%s",
                result.get("backend"),
                result.get("reason"),
            )
            errors.append(OBJECT_CLEANUP_FAILED)
        except Exception as exc:
            logger.warning("failed to cleanup object during gallery person deletion: %s", exception_log_summary(exc))
            errors.append(OBJECT_CLEANUP_FAILED)
    return deleted_count, errors


def restore_gallery_person_state(person: PersonRecord, previous_person: PersonRecord) -> None:
    person.tenant_id = previous_person.tenant_id
    person.person_id = previous_person.person_id
    person.display_name = previous_person.display_name
    person.metadata = deepcopy(previous_person.metadata)
    person.features = deepcopy(previous_person.features)
    person.created_at = previous_person.created_at
    person.updated_at = previous_person.updated_at


def restore_gallery_person_snapshot(
    tenant_id: str,
    person_id: str,
    previous_person: PersonRecord | None,
) -> list[str]:
    errors: list[str] = []
    key = gallery_key(tenant_id, person_id)
    if previous_person is None:
        GALLERY.pop(key, None)
        try:
            persist_person_delete(tenant_id, person_id)
        except Exception as exc:
            logger.warning("failed to persist restored empty gallery person deletion: %s", exception_log_summary(exc))
            errors.append("delete restored empty gallery person failed")
        return errors

    restored_person = deepcopy(previous_person)
    GALLERY.pop(key, None)
    try:
        persist_person_delete(tenant_id, person_id)
    except Exception as exc:
        logger.warning("failed to persist mutated gallery person deletion before restore: %s", exception_log_summary(exc))
        errors.append("delete mutated gallery person before restore failed")
    GALLERY[key] = restored_person
    try:
        persist_person(restored_person)
        for feature in restored_person.features:
            persist_feature(restored_person, feature)
    except Exception as exc:
        logger.warning("failed to persist restored gallery person snapshot: %s", exception_log_summary(exc))
        errors.append("restore gallery person failed")
    return errors


def raise_gallery_rollback_failure(original_error: Exception, rollback_errors: list[str]) -> None:
    raise_rollback_failure("gallery mutation failed and rollback persistence failed", original_error, rollback_errors)


def rollback_gallery_mutation(
    *,
    tenant_id: str,
    person_id: str,
    previous_person: PersonRecord | None,
    created_object_infos: list[dict[str, Any]],
    original_error: Exception,
) -> None:
    rollback_errors: list[str] = []
    for object_info in reversed(created_object_infos):
        object_error = cleanup_object_after_failed_feature(object_info)
        if object_error:
            rollback_errors.append(object_error)
    rollback_errors.extend(restore_gallery_person_snapshot(tenant_id, person_id, previous_person))
    if rollback_errors:
        raise_gallery_rollback_failure(original_error, rollback_errors)


@router.post("/v1/gallery/enroll", dependencies=[Depends(permission_dependency("gallery:write"))])
async def v1_gallery_enroll(
    files: list[UploadFile] = File(...),
    person_id: str | None = Form(None),
    display_name: str | None = Form(None),
    modality: str = Form("body"),
    metadata: str | None = Form(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    modality = validate_gallery_modality(modality)
    if len(files) > MAX_EMBEDDING_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"too many image files: {len(files)}, max {MAX_EMBEDDING_IMAGES}",
        )
    decoded = await decode_upload_images(files)
    previous_person = None
    if person_id is not None:
        previous_person = deepcopy(GALLERY.get(gallery_key(tenant_id, person_id)))
    person = await run_blocking_io(upsert_person, person_id, display_name, parse_metadata(metadata), tenant_id=tenant_id)
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
                OBJECT_STORE.put_bytes,
                tenant_id,
                "gallery-image",
                item.frame.filename,
                item.data or b"",
            )
            created_object_infos.append(object_info)
            embedding, quality_score, model_id, model_version = await extract_gallery_embedding(item.image, modality)
            feature = await run_blocking_io(
                add_feature,
                person,
                modality=modality,
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
            audit_event,
            "gallery_enroll",
            request_id=request_id,
            tenant_id=tenant_id,
            person_id=person.person_id,
            modality=modality,
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
        )
        raise
    return portrait_success(
        request_id,
        {
            "person": person.public_dict(include_embeddings=False),
            "features": created,
            "feature_count": len(created),
            "input_file_count": len(decoded),
            "skipped_duplicates": skipped_duplicates,
            "skipped_duplicate_count": len(skipped_duplicates),
            "store_backend": store_backend_name(),
        },
    )


@router.post("/v1/gallery/search", dependencies=[Depends(permission_dependency("gallery:read"))])
async def v1_gallery_search(
    file: UploadFile = File(...),
    modality: str = Form("body"),
    top_k: int = Form(5),
    threshold_profile: str = Form("normal"),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    top_k = validate_int_range("top_k", top_k, minimum=1, maximum=100)
    threshold_profile = validate_threshold_profile(threshold_profile)
    modality = validate_gallery_modality(modality)
    decoded = await decode_upload_image(file)
    embedding, quality_score, _, _ = await extract_gallery_embedding(decoded.image, modality)
    frame_quality_score = float(decoded.frame.quality.get("score", 0.0)) if isinstance(decoded.frame.quality, dict) else None
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
    retrieval_context = candidates[0].get("retrieval_context", {}) if candidates else {}
    await run_blocking_io(
        audit_event,
        "gallery_search",
        request_id=request_id,
        tenant_id=tenant_id,
        modality=modality,
        candidate_count=len(candidates),
    )
    return portrait_success(
        request_id,
        {
            "candidates": candidates,
            "candidate_count": len(candidates),
            "query": {
                "modality": modality,
                "quality_score": round(float(quality_score), 6),
                "frame_quality_score": round(frame_quality_score, 6) if frame_quality_score is not None else None,
                "combined_quality_score": combined_quality_score,
                "threshold_profile": threshold_profile,
                "top_k": top_k,
                "retrieval_context": retrieval_context,
            },
            "store_backend": store_backend_name(),
        },
    )


@router.post("/v1/gallery/search/batch", dependencies=[Depends(permission_dependency("gallery:read"))])
async def v1_gallery_search_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    modality: str = Form("body"),
    top_k: int = Form(5),
    threshold_profile: str = Form("normal"),
    async_mode: bool = Form(False),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    top_k = validate_int_range("top_k", top_k, minimum=1, maximum=100)
    threshold_profile = validate_threshold_profile(threshold_profile)
    modality = validate_gallery_modality(modality)
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one image file is required")
    if len(files) > MAX_EMBEDDING_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"too many image files: {len(files)}, max {MAX_EMBEDDING_IMAGES}",
        )
    if async_mode:
        file_payloads = [(file.filename, file.content_type, await file.read()) for file in files]
        job = await run_blocking_io(
            create_batch_job,
            "gallery_search_batch",
            tenant_id,
            metadata={"query_count": len(file_payloads), "modality": modality, "threshold_profile": threshold_profile},
        )

        async def handler(batch_job: VideoJob) -> dict[str, Any]:
            from io import BytesIO
            from fastapi import UploadFile
            from starlette.datastructures import Headers

            async def update_progress(progress: float) -> None:
                batch_job.progress = progress
                await run_blocking_io(persist_video_job, batch_job)

            upload_files = [
                UploadFile(filename=name, file=BytesIO(data), headers=Headers({"content-type": ctype or "application/octet-stream"}))
                for name, ctype, data in file_payloads
            ]
            results = await gallery_search_batch_results(
                upload_files,
                modality=modality,
                top_k=top_k,
                threshold_profile=threshold_profile,
                tenant_id=tenant_id,
                progress_callback=update_progress,
            )
            return {
                "results": results,
                "query_count": len(results),
                "store_backend": store_backend_name(),
            }

        background_tasks.add_task(run_batch_job, job.job_id, tenant_id, handler)
        return portrait_success(request_id, {"batch_id": job.job_id, "job": job.public_dict(include_result=False)})
    results = await gallery_search_batch_results(
        files,
        modality=modality,
        top_k=top_k,
        threshold_profile=threshold_profile,
        tenant_id=tenant_id,
    )
    await run_blocking_io(
        audit_event,
        "gallery_search_batch",
        request_id=request_id,
        tenant_id=tenant_id,
        modality=modality,
        query_count=len(results),
        candidate_count=sum(item["candidate_count"] for item in results),
    )
    return portrait_success(
        request_id,
        {
            "results": results,
            "query_count": len(results),
            "store_backend": store_backend_name(),
        },
    )


@router.post("/v1/gallery/reindex", dependencies=[Depends(permission_dependency("gallery:write"))])
async def v1_gallery_reindex(
    modality: str | None = Query(None),
    model_id: str | None = Query(None, max_length=128),
    dry_run: bool = Query(False),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    modality_key = validate_gallery_modality(modality) if modality is not None else None
    result = await run_blocking_io(
        reindex_gallery_vectors,
        tenant_id=tenant_id,
        modality=modality_key,
        model_id=model_id,
        dry_run=dry_run,
    )
    await run_blocking_io(
        audit_event,
        "gallery_reindex",
        request_id=request_id,
        tenant_id=tenant_id,
        outcome="success" if result["failed_feature_count"] == 0 else "partial_failure",
        person_count=result["person_count"],
        feature_count=result["feature_count"],
        matched_feature_count=result["matched_feature_count"],
        reindexed_feature_count=result["reindexed_feature_count"],
        failed_feature_count=result["failed_feature_count"],
        dry_run=result["dry_run"],
        modality=modality_key,
        model_id=result["filters"]["model_id"],
        vector_backend=result["vector_backend"],
    )
    return portrait_success(
        request_id,
        {
            **result,
            "store_backend": store_backend_name(),
        },
    )


@router.get("/v1/gallery/{person_id}", dependencies=[Depends(permission_dependency("gallery:read"))])
async def v1_gallery_get_person(
    person_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    person = get_person_or_404(person_id, tenant_id=tenant_id)
    return portrait_success(request_id, {"person": person.public_dict(include_embeddings=False)})


@router.patch("/v1/gallery/{person_id}", dependencies=[Depends(permission_dependency("gallery:write"))])
async def v1_gallery_patch_person(
    person_id: str,
    payload: GalleryPatchRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    update_payload = payload.model_dump(exclude_unset=True)
    if not update_payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="patch payload must not be empty")
    if "metadata" in update_payload:
        if update_payload["metadata"] is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be a JSON object")
        update_payload["metadata"] = normalize_public_metadata(update_payload["metadata"], field_name="metadata")
    previous_person = deepcopy(get_person_or_404(person_id, tenant_id=tenant_id))
    person = await run_blocking_io(patch_person, person_id, update_payload, tenant_id=tenant_id)
    try:
        await run_blocking_io(audit_event, "gallery_patch_person", request_id=request_id, tenant_id=tenant_id, person_id=person_id)
    except Exception as exc:
        await run_blocking_io(
            rollback_gallery_mutation,
            tenant_id=tenant_id,
            person_id=person.person_id,
            previous_person=previous_person,
            created_object_infos=[],
            original_error=exc,
        )
        raise
    return portrait_success(request_id, {"person": person.public_dict(include_embeddings=False)})


@router.delete("/v1/gallery/{person_id}", dependencies=[Depends(permission_dependency("gallery:write"))])
async def v1_gallery_delete_person(
    person_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    previous_person = deepcopy(get_person_or_404(person_id, tenant_id=tenant_id))
    await run_blocking_io(
        audit_event,
        "gallery_delete_person_requested",
        request_id=request_id,
        tenant_id=tenant_id,
        outcome="started",
        person_id=person_id,
        feature_count=len(previous_person.features),
        object_reference_count=len(feature_object_infos(previous_person)),
    )
    deleted = await run_blocking_io(delete_person, person_id, tenant_id=tenant_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person not found")
    deleted_object_count, object_cleanup_errors = await run_blocking_io(cleanup_gallery_feature_objects, previous_person)
    if object_cleanup_errors:
        rollback_errors = await run_blocking_io(restore_gallery_person_snapshot, tenant_id, previous_person.person_id, previous_person)
        if rollback_errors:
            raise_gallery_rollback_failure(HTTPException(status_code=503, detail=OBJECT_CLEANUP_FAILED), rollback_errors)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=OBJECT_CLEANUP_FAILED)
    return portrait_success(
        request_id,
        {
            "deleted": True,
            "person_id": person_id,
            "deleted_object_count": deleted_object_count,
        },
    )
