from copy import deepcopy
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_gallery import (
    GALLERY,
    delete_person,
    feature_object_infos,
    get_person_or_404,
    patch_person,
    persist_feature,
    persist_person,
    persist_person_delete,
    reindex_gallery_vectors,
)
from app.portrait_gallery_orchestration import (
    SUPPORTED_GALLERY_MODALITIES,
    create_async_gallery_search_batch,
    enroll_gallery_person,
    search_gallery_batch,
    search_gallery_image,
    validate_gallery_modality,
)
from app.portrait_gallery_mutations import (
    cleanup_gallery_feature_objects,
    raise_gallery_rollback_failure,
    restore_gallery_person_snapshot,
    rollback_gallery_mutation,
)
from app.portrait_response import OBJECT_CLEANUP_FAILED, portrait_success
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_request_validation import validate_int_range
from app.portrait_security import normalize_public_metadata
from app.portrait_storage import store_backend_name
from app.portrait_object_storage import OBJECT_STORE
from app.security import require_api_token


router = APIRouter(dependencies=[Depends(require_api_token)])



class GalleryPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] | None = None


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
    payload = await enroll_gallery_person(
        files,
        person_id=person_id,
        display_name=display_name,
        modality=modality,
        metadata=metadata,
        request_id=request_id,
        tenant_id=ctx.tenant_id,
    )
    return portrait_success(request_id, payload)

@router.post("/v1/gallery/search", dependencies=[Depends(permission_dependency("gallery:read"))])
async def v1_gallery_search(
    file: UploadFile = File(...),
    modality: str = Form("body"),
    top_k: int = Form(5),
    threshold_profile: str = Form("normal"),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    top_k = validate_int_range("top_k", top_k, minimum=1, maximum=100)
    payload = await search_gallery_image(
        file,
        modality=modality,
        top_k=top_k,
        threshold_profile=threshold_profile,
        request_id=request_id,
        tenant_id=ctx.tenant_id,
    )
    return portrait_success(request_id, payload)

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
    if async_mode:
        job = await create_async_gallery_search_batch(
            background_tasks,
            files,
            modality=modality,
            top_k=top_k,
            threshold_profile=threshold_profile,
            tenant_id=tenant_id,
        )
        return portrait_success(request_id, {"batch_id": job.job_id, "job": job.public_dict(include_result=False)})
    payload = await search_gallery_batch(
        files,
        modality=modality,
        top_k=top_k,
        threshold_profile=threshold_profile,
        request_id=request_id,
        tenant_id=tenant_id,
    )
    return portrait_success(request_id, payload)

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
            object_store=OBJECT_STORE,
            gallery=GALLERY,
            persist_delete_hook=persist_person_delete,
            persist_person_hook=persist_person,
            persist_feature_hook=persist_feature,
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
    deleted_object_count, object_cleanup_errors = await run_blocking_io(
        cleanup_gallery_feature_objects,
        previous_person,
        object_store=OBJECT_STORE,
    )
    if object_cleanup_errors:
        rollback_errors = await run_blocking_io(
            restore_gallery_person_snapshot,
            tenant_id,
            previous_person.person_id,
            previous_person,
            gallery=GALLERY,
            persist_delete_hook=persist_person_delete,
            persist_person_hook=persist_person,
            persist_feature_hook=persist_feature,
        )
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
