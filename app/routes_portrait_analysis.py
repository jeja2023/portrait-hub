from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.portrait_analysis_archive import (
    ARCHIVE_SOURCE_TYPES,
    get_analysis_artifact,
    list_analysis_archives,
    public_analysis_archive,
)
from app.portrait_async import run_blocking_io
from app.portrait_auth import permission_dependency
from app.portrait_object_storage import OBJECT_STORE
from app.portrait_pagination import normalize_list_pagination
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])


@router.get(
    "/v1/analysis/results",
    dependencies=[Depends(permission_dependency("infer"))],
)
async def v1_list_analysis_results(
    source_type: str | None = Query(None),
    mode: str | None = Query(None, max_length=64),
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    if source_type is not None and source_type not in ARCHIVE_SOURCE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持的解析档案来源类型",
        )
    pagination_request = normalize_list_pagination(limit, offset, cursor)
    records, pagination = await run_blocking_io(
        list_analysis_archives,
        ctx.tenant_id,
        source_type=source_type,
        mode=mode,
        limit=pagination_request.limit,
        offset=pagination_request.offset,
        cursor=pagination_request.cursor,
    )
    results = await run_blocking_io(
        lambda: [public_analysis_archive(record) for record in records]
    )
    return portrait_success(
        ctx.request_id,
        {"results": results, "archives": results, **pagination},
    )


@router.get(
    "/v1/analysis/artifacts/{archive_id}/{artifact_id}",
    dependencies=[Depends(permission_dependency("infer"))],
)
async def v1_get_analysis_artifact(
    archive_id: str,
    artifact_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> Response:
    if len(archive_id) > 80 or len(artifact_id) > 80:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析结果图片不存在")
    artifact = await run_blocking_io(
        get_analysis_artifact, ctx.tenant_id, archive_id, artifact_id
    )
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析结果图片不存在")
    content = await run_blocking_io(OBJECT_STORE.get_bytes, artifact.object_info)
    return Response(
        content=content,
        media_type=artifact.media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


__all__ = ["router"]
