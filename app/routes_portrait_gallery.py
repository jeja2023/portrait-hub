from copy import deepcopy
from typing import Any

from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.api_contracts import ContractAPIRouter as APIRouter
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_gallery import (
    GALLERY,
    add_feature,
    delete_person,
    feature_object_infos,
    get_person_or_404,
    patch_person,
    persist_feature,
    persist_person,
    persist_person_delete,
    reindex_gallery_vectors,
)
from app.portrait_gallery_mutations import (
    cleanup_gallery_feature_objects,
    raise_gallery_rollback_failure,
    restore_gallery_person_snapshot,
    rollback_gallery_mutation,
)
from app.portrait_gallery_orchestration import (
    create_async_gallery_search_batch,
    enroll_gallery_person,
    search_gallery_batch,
    search_gallery_image,
    validate_gallery_modality,
)
from app.portrait_object_storage import OBJECT_STORE
from app.portrait_pagination import normalize_list_pagination, page_items_keyset
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_request_validation import validate_int_range
from app.portrait_response import OBJECT_CLEANUP_FAILED, portrait_success
from app.portrait_runtime_store import gallery_people_snapshots
from app.portrait_security import normalize_public_metadata
from app.portrait_storage import store_backend_name
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])


class GalleryPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] | None = None


@router.get("/v1/gallery", dependencies=[Depends(permission_dependency("gallery:read"))])
async def v1_gallery_list_people(
    query: str | None = Query(None, max_length=256),
    modality: str | None = Query(None),
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    modality_key = validate_gallery_modality(modality) if modality is not None else None
    normalized_query = query.strip().casefold() if query is not None else ""
    rows: list[dict[str, Any]] = []
    for person in gallery_people_snapshots(ctx.scope_id):
        if (
            normalized_query
            and normalized_query not in person.person_id.casefold()
            and normalized_query not in str(person.display_name or "").casefold()
        ):
            continue
        visible_features = [
            feature for feature in person.features if modality_key is None or feature.modality == modality_key
        ]
        if modality_key is not None and not visible_features:
            continue
        primary_feature = max(
            visible_features,
            key=lambda feature: (float(feature.quality_score), float(feature.created_at), feature.feature_id),
            default=None,
        )
        thumbnail = None
        if primary_feature is not None:
            thumbnail = primary_feature.public_dict(include_embedding=False).get("thumbnail")
        public_person = person.public_dict(include_embeddings=False)
        rows.append(
            {
                "person_id": person.person_id,
                "display_name": person.display_name,
                "metadata": public_person["metadata"],
                "feature_count": len(person.features),
                "modalities": sorted({feature.modality for feature in person.features}),
                "created_at": person.created_at,
                "updated_at": person.updated_at,
                "thumbnail": thumbnail,
                "sort_updated_at": -float(person.updated_at),
            }
        )

    rows.sort(key=lambda item: (item["sort_updated_at"], item["person_id"]))
    pagination_request = normalize_list_pagination(limit, offset, cursor)
    page, pagination = page_items_keyset(
        rows,
        limit=pagination_request.limit,
        offset=pagination_request.offset,
        cursor=pagination_request.cursor,
        key_fields=["sort_updated_at", "person_id"],
    )
    public_page = [{key: value for key, value in item.items() if key != "sort_updated_at"} for item in page]
    return portrait_success(ctx.request_id, {"items": public_page, "people": public_page, **pagination})


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
        tenant_id=ctx.scope_id,
        object_store=OBJECT_STORE,
        audit_hook=audit_event,
        add_feature_hook=add_feature,
        persist_delete_hook=persist_person_delete,
        persist_person_hook=persist_person,
        persist_feature_hook=persist_feature,
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
        tenant_id=ctx.scope_id,
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
    tenant_id = ctx.scope_id
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
    tenant_id = ctx.scope_id
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
    tenant_id = ctx.scope_id
    person = get_person_or_404(person_id, tenant_id=tenant_id)
    return portrait_success(request_id, {"person": person.public_dict(include_embeddings=False)})


@router.patch("/v1/gallery/{person_id}", dependencies=[Depends(permission_dependency("gallery:write"))])
async def v1_gallery_patch_person(
    person_id: str,
    payload: GalleryPatchRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.scope_id
    update_payload = payload.model_dump(exclude_unset=True)
    if not update_payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="补丁请求体不能为空")
    if "metadata" in update_payload:
        if update_payload["metadata"] is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="元数据必须是 JSON 对象")
        update_payload["metadata"] = normalize_public_metadata(update_payload["metadata"], field_name="metadata")
    previous_person = deepcopy(get_person_or_404(person_id, tenant_id=tenant_id))
    person = await run_blocking_io(patch_person, person_id, update_payload, tenant_id=tenant_id)
    try:
        await run_blocking_io(
            audit_event, "gallery_patch_person", request_id=request_id, tenant_id=tenant_id, person_id=person_id
        )
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
    tenant_id = ctx.scope_id
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="人员不存在")
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
            raise_gallery_rollback_failure(
                HTTPException(status_code=503, detail=OBJECT_CLEANUP_FAILED), rollback_errors
            )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=OBJECT_CLEANUP_FAILED)
    return portrait_success(
        request_id,
        {
            "deleted": True,
            "person_id": person_id,
            "deleted_object_count": deleted_object_count,
        },
    )
