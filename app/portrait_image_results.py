from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from uuid import uuid4

from PIL import Image

from app.observability import logger, wall_time
from app.portrait_response import exception_log_summary
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import (
    IMAGE_ANALYSIS_THUMBNAIL_MAX_SIDE,
    MAX_IMAGE_ANALYSIS_RESULTS_PER_TENANT,
    PORTRAIT_IMAGE_RESULTS_STATE_PATH,
    PORTRAIT_STORAGE_BACKEND,
)


@dataclass
class ImageAnalysisResult:
    result_id: str
    tenant_id: str
    request_id: str
    mode: str
    endpoint: str
    payload: dict[str, Any]
    previews: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=wall_time)

    def public_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "request_id": self.request_id,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "payload": deepcopy(self.payload),
            "previews": deepcopy(self.previews),
            "created_at": self.created_at,
        }

    def state_dict(self) -> dict[str, Any]:
        return {
            **self.public_dict(),
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> ImageAnalysisResult:
        result_payload = payload.get("payload")
        previews = payload.get("previews")
        return cls(
            result_id=str(payload["result_id"]),
            tenant_id=str(payload.get("tenant_id") or "default"),
            request_id=str(payload.get("request_id") or ""),
            mode=str(payload.get("mode") or "image"),
            endpoint=str(payload.get("endpoint") or ""),
            payload=deepcopy(result_payload) if isinstance(result_payload, dict) else {},
            previews=(
                [deepcopy(item) for item in previews if isinstance(item, dict)]
                if isinstance(previews, list)
                else []
            ),
            created_at=float(payload.get("created_at") or wall_time()),
        )


ImageResultKey = tuple[str, str]


IMAGE_ANALYSIS_RESULTS: dict[ImageResultKey, ImageAnalysisResult] = {}
IMAGE_ANALYSIS_RESULTS_LOCK = threading.RLock()


def image_result_key(tenant_id: str, result_id: str) -> ImageResultKey:
    return (str(tenant_id), str(result_id))


def postgres_image_results_enabled() -> bool:
    return PORTRAIT_STORAGE_BACKEND == "postgres"


def image_analysis_results_snapshot(
    tenant_id: str | None = None,
) -> list[ImageAnalysisResult]:
    with IMAGE_ANALYSIS_RESULTS_LOCK:
        records = [
            record
            for record in IMAGE_ANALYSIS_RESULTS.values()
            if tenant_id is None or record.tenant_id == tenant_id
        ]
        return [
            deepcopy(record)
            for record in sorted(
                records,
                key=lambda item: (-item.created_at, item.result_id),
            )
        ]


def image_results_state_payload() -> dict[str, Any]:
    with IMAGE_ANALYSIS_RESULTS_LOCK:
        return {
            "version": 1,
            "results": [
                record.state_dict()
                for record in sorted(
                    IMAGE_ANALYSIS_RESULTS.values(),
                    key=lambda item: (item.tenant_id, item.created_at, item.result_id),
                )
            ],
        }


def save_image_analysis_results_state() -> None:
    write_json_state(PORTRAIT_IMAGE_RESULTS_STATE_PATH, image_results_state_payload())


def _thumbnail_data_url(image: Any) -> tuple[str | None, int, int]:
    if not isinstance(image, Image.Image):
        return None, 0, 0
    preview = image.convert("RGB")
    width, height = preview.size
    max_side = max(1, int(IMAGE_ANALYSIS_THUMBNAIL_MAX_SIDE))
    preview.thumbnail((max_side, max_side))
    buffer = BytesIO()
    preview.save(buffer, format="JPEG", quality=78, optimize=True)
    import base64

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}", width, height


def image_preview_items(
    images: list[Any], filenames: list[str | None] | None = None
) -> list[dict[str, Any]]:
    _ = filenames
    previews: list[dict[str, Any]] = []
    for index, image in enumerate(images):
        source, width, height = _thumbnail_data_url(image)
        if source is None:
            continue
        name = f"image-{index + 1}"
        previews.append(
            {
                "name": name,
                "label": f"{index + 1}. {name}",
                "src": source,
                "width": width,
                "height": height,
            }
        )
    return previews


def _tenant_result_keys(tenant_id: str) -> list[ImageResultKey]:
    records = [
        record
        for record in IMAGE_ANALYSIS_RESULTS.values()
        if record.tenant_id == tenant_id
    ]
    records.sort(key=lambda item: (-item.created_at, item.result_id))
    limit = max(1, int(MAX_IMAGE_ANALYSIS_RESULTS_PER_TENANT))
    return [
        image_result_key(record.tenant_id, record.result_id)
        for record in records[limit:]
    ]


def persist_image_analysis_result(record: ImageAnalysisResult) -> None:
    if postgres_image_results_enabled():
        from app.portrait_postgres import upsert_image_analysis_result

        upsert_image_analysis_result(
            record.state_dict(),
            max_results=max(1, int(MAX_IMAGE_ANALYSIS_RESULTS_PER_TENANT)),
        )
        return
    save_image_analysis_results_state()


def create_image_analysis_result(
    *,
    tenant_id: str,
    request_id: str,
    mode: str,
    endpoint: str,
    payload: dict[str, Any],
    images: list[Any],
    filenames: list[str | None] | None = None,
) -> ImageAnalysisResult:
    record = ImageAnalysisResult(
        result_id=f"imgres_{uuid4().hex[:16]}",
        tenant_id=tenant_id,
        request_id=request_id,
        mode=mode,
        endpoint=endpoint,
        payload=deepcopy(payload),
        previews=image_preview_items(images, filenames),
    )
    with IMAGE_ANALYSIS_RESULTS_LOCK:
        key = image_result_key(record.tenant_id, record.result_id)
        IMAGE_ANALYSIS_RESULTS[key] = record
        removed = {
            stale_key: IMAGE_ANALYSIS_RESULTS.pop(stale_key)
            for stale_key in _tenant_result_keys(record.tenant_id)
        }
        try:
            persist_image_analysis_result(record)
        except Exception:
            IMAGE_ANALYSIS_RESULTS.pop(key, None)
            IMAGE_ANALYSIS_RESULTS.update(removed)
            raise
    return deepcopy(record)


def load_image_analysis_results_state() -> None:
    if postgres_image_results_enabled():
        from app.portrait_postgres import load_image_analysis_results_snapshot

        payload: Any = {"results": load_image_analysis_results_snapshot()}
    else:
        payload = read_json_state(
            PORTRAIT_IMAGE_RESULTS_STATE_PATH,
            {"results": []},
        )
    if not isinstance(payload, dict):
        handle_state_read_error(
            f"image results state root must be a mapping: {PORTRAIT_IMAGE_RESULTS_STATE_PATH}"
        )
        return
    results = payload.get("results", [])
    if not isinstance(results, list):
        handle_state_read_error(
            f"image results state results must be a list: {PORTRAIT_IMAGE_RESULTS_STATE_PATH}"
        )
        return
    restored: dict[ImageResultKey, ImageAnalysisResult] = {}
    for item in results:
        if not isinstance(item, dict) or "result_id" not in item:
            continue
        try:
            record = ImageAnalysisResult.from_state(item)
        except Exception as exc:
            logger.warning(
                "skipping invalid image analysis result state: %s",
                exception_log_summary(exc),
            )
            continue
        restored[image_result_key(record.tenant_id, record.result_id)] = record
    with IMAGE_ANALYSIS_RESULTS_LOCK:
        IMAGE_ANALYSIS_RESULTS.clear()
        IMAGE_ANALYSIS_RESULTS.update(restored)
        for tenant_id in {record.tenant_id for record in restored.values()}:
            for stale_key in _tenant_result_keys(tenant_id):
                IMAGE_ANALYSIS_RESULTS.pop(stale_key, None)


__all__ = [
    "IMAGE_ANALYSIS_RESULTS",
    "ImageAnalysisResult",
    "create_image_analysis_result",
    "image_analysis_results_snapshot",
    "image_result_key",
    "load_image_analysis_results_state",
    "persist_image_analysis_result",
]
