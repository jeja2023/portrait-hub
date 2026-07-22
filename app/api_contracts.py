from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Literal, get_args, get_origin

from fastapi import APIRouter
from fastapi.datastructures import DefaultPlaceholder
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class ExtensibleContractModel(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


class PortraitSuccess[DataT](StrictContractModel):
    status: Literal["success"]
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1)
    data: DataT
    warnings: list[str] | None = None
    meta: GenericData | None = None


class PortraitErrorDetail(ExtensibleContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: Any | None = None


class PortraitErrorResponse(StrictContractModel):
    status: Literal["error"]
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1)
    error: PortraitErrorDetail


class GenericData(ExtensibleContractModel):
    pass


PortraitSuccessResponse = PortraitSuccess[GenericData]


class QualityContract(ExtensibleContractModel):
    score: float | None = None


class ModelSummaryContract(ExtensibleContractModel):
    id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    version: str | None = None
    adapter: str | None = None
    embedding_dim: int | None = Field(default=None, ge=0)


class MediaFrameContract(ExtensibleContractModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    pts_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    image_index: int = Field(ge=0)
    quality: QualityContract | None = None
    duplicate_of: str | None = None
    duplicate_distance: int | None = Field(default=None, ge=0)


class FaceContract(ExtensibleContractModel):
    face_index: int = Field(ge=0)
    box: list[float] = Field(min_length=4, max_length=4)
    score: float = Field(ge=0, le=1)
    landmarks: list[list[float]] = Field(default_factory=list)
    quality: QualityContract
    embedding_dim: int = Field(ge=0)
    embedding: list[float] | None = None
    detection_strategy: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str | None = None
    model_status: str = Field(min_length=1)


class PersonContract(ExtensibleContractModel):
    box: list[float] = Field(min_length=4, max_length=4)
    score: float = Field(ge=0, le=1)
    quality: QualityContract
    embedding_dim: int = Field(ge=0)
    embedding: list[float] | None = None
    selection_strategy: str = Field(min_length=1)
    model_status: str = Field(min_length=1)


class KeypointContract(ExtensibleContractModel):
    name: str = Field(min_length=1)
    point: list[float] = Field(min_length=2, max_length=2)
    score: float = Field(ge=0, le=1)


class PoseContract(ExtensibleContractModel):
    quality: QualityContract
    keypoints: list[KeypointContract]
    skeleton: list[list[str]]
    model_status: str = Field(min_length=1)


class ColorContract(ExtensibleContractModel):
    name: str = Field(min_length=1)
    rgb: list[int] = Field(min_length=3, max_length=3)


class AppearanceContract(ExtensibleContractModel):
    quality: QualityContract
    dominant_color: ColorContract
    attributes: GenericData
    embedding_dim: int = Field(ge=0)
    model_status: str = Field(min_length=1)
    embedding: list[float] | None = None


class FaceFrameContract(MediaFrameContract):
    faces: list[FaceContract]
    face_count: int = Field(ge=0)


class PersonFrameContract(MediaFrameContract):
    persons: list[PersonContract]
    person_count: int = Field(ge=0)


class PoseFrameContract(MediaFrameContract):
    pose: PoseContract


class AppearanceFrameContract(MediaFrameContract):
    appearance: AppearanceContract


class InferFacesData(StrictContractModel):
    frames: list[FaceFrameContract]
    frame_count: int = Field(ge=0)
    face_count: int = Field(ge=0)
    model: ModelSummaryContract


class InferPersonsData(StrictContractModel):
    frames: list[PersonFrameContract]
    frame_count: int = Field(ge=0)
    person_count: int = Field(ge=0)
    model: ModelSummaryContract


class InferPoseData(StrictContractModel):
    frames: list[PoseFrameContract]
    frame_count: int = Field(ge=0)
    model: ModelSummaryContract


class InferAppearanceData(StrictContractModel):
    frames: list[AppearanceFrameContract]
    frame_count: int = Field(ge=0)
    model: ModelSummaryContract


class GaitTrackletContract(ExtensibleContractModel):
    frame_count: int = Field(ge=0)
    quality: float | None = None
    reason: str | None = None
    embedding_dim: int = Field(ge=0)
    embedding: list[float] | None = None


class InferGaitData(StrictContractModel):
    tracklet: GaitTrackletContract
    model: ModelSummaryContract


class TimingContract(ExtensibleContractModel):
    decode_seconds: float | None = Field(default=None, ge=0)
    preprocess_seconds: float | None = Field(default=None, ge=0)
    queue_seconds: float | None = Field(default=None, ge=0)
    load_seconds: float | None = Field(default=None, ge=0)
    inference_seconds: float | None = Field(default=None, ge=0)
    postprocess_seconds: float | None = Field(default=None, ge=0)
    total_seconds: float | None = Field(default=None, ge=0)


class VisionModelContract(ExtensibleContractModel):
    id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    task: Literal["detection", "classification", "reid"]
    alias: str | None = None
    traffic_key: str | None = None
    project_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    type: str | None = None
    runtime: str | None = None
    version: str | None = None
    precision: str | None = None
    hash: str = Field(min_length=1)


class VisionResultContract(ExtensibleContractModel):
    image_index: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    detection_count: int | None = Field(default=None, ge=0)
    prediction_count: int | None = Field(default=None, ge=0)
    embedding_dim: int | None = Field(default=None, ge=0)
    detections: list[GenericData] | None = None
    predictions: list[GenericData] | None = None
    embedding: list[float] | None = None


class VisionInferData(StrictContractModel):
    model: VisionModelContract
    cold_loaded: bool
    timing: TimingContract
    input_shape: list[Any]
    output_shapes: list[list[Any]]
    inference_mode: str = Field(min_length=1)
    parameters: GenericData
    results: list[VisionResultContract]
    image_count: int = Field(ge=0)
    result_count: int = Field(ge=0)


class TrackedPersonContract(ExtensibleContractModel):
    box: list[float] = Field(min_length=4, max_length=4)
    score: float = Field(ge=0, le=1)
    quality: GenericData | None = None
    crop_quality: GenericData | None = None
    track_id: str | None = None
    embedding_dim: int | None = Field(default=None, ge=0)
    embedding_index: int | None = Field(default=None, ge=0)
    embedding: list[float] | None = None


class TrackFrameContract(ExtensibleContractModel):
    frame_index: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    persons: list[TrackedPersonContract]
    person_count: int = Field(ge=0)


class TrackContract(ExtensibleContractModel):
    track_id: str = Field(min_length=1)
    frame_count: int = Field(ge=1)
    first_frame_index: int = Field(ge=0)
    last_frame_index: int = Field(ge=0)
    average_confidence: float = Field(ge=0, le=1)
    average_quality: float = Field(ge=0, le=1)
    gap_count: int = Field(ge=0)
    max_gap: int = Field(ge=0)
    interpolated_count: int = Field(ge=0)
    stability_score: float = Field(ge=0, le=1)
    tracklet_quality_score: float = Field(ge=0, le=1)
    association_quality: GenericData
    template: GenericData
    interpolated: list[GenericData]


class ColdLoadedContract(StrictContractModel):
    detector: bool
    reid: bool


class RuntimeShapeContract(ExtensibleContractModel):
    input_shape: list[Any]
    output_shapes: list[list[Any]]
    inference_mode: str = Field(min_length=1)
    embedding_dim: int | None = Field(default=None, ge=0)
    embedding_count: int | None = Field(default=None, ge=0)


class InferTracksData(StrictContractModel):
    detector_model: str = Field(min_length=1)
    reid_model: str = Field(min_length=1)
    cold_loaded: ColdLoadedContract
    timing: TimingContract
    detector: RuntimeShapeContract
    reid: RuntimeShapeContract
    frames: list[TrackFrameContract]
    tracks: list[TrackContract]
    track_count: int = Field(ge=0)
    tracker: GenericData
    frame_count: int = Field(ge=0)
    person_count: int = Field(ge=0)
    embedding_count: int = Field(ge=0)


class AnalysisPreviewContract(ExtensibleContractModel):
    artifact_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    content_url: str = Field(min_length=1)


class AnalysisArchiveContract(ExtensibleContractModel):
    archive_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    source_type: Literal["image", "video", "stream"]
    source_ref: str
    mode: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    payload: GenericData
    previews: list[GenericData]
    artifact_count: int = Field(ge=0)
    source_artifacts: list[AnalysisPreviewContract]
    created_at: float


class AnalysisListData(StrictContractModel):
    results: list[AnalysisArchiveContract]
    archives: list[AnalysisArchiveContract]
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    count: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    cursor: str | None = None
    next_cursor: str | None = None
    has_more: bool


class AnalysisDetailData(StrictContractModel):
    result: AnalysisArchiveContract


InferFacesResponse = PortraitSuccess[InferFacesData]
InferPersonsResponse = PortraitSuccess[InferPersonsData]
InferPoseResponse = PortraitSuccess[InferPoseData]
InferAppearanceResponse = PortraitSuccess[InferAppearanceData]
InferGaitResponse = PortraitSuccess[InferGaitData]
AnalysisListResponse = PortraitSuccess[AnalysisListData]
AnalysisDetailResponse = PortraitSuccess[AnalysisDetailData]
VisionInferResponse = PortraitSuccess[VisionInferData]
InferTracksResponse = PortraitSuccess[InferTracksData]


def _returns_raw_response(endpoint: Callable[..., Any]) -> bool:
    annotation = inspect.signature(endpoint).return_annotation
    if annotation is inspect.Signature.empty:
        return False
    candidates = get_args(annotation) if get_origin(annotation) is not None else (annotation,)
    return any(isinstance(candidate, type) and issubclass(candidate, Response) for candidate in candidates)


_ERROR_RESPONSES = {
    code: {"model": PortraitErrorResponse, "description": "Portrait Hub error envelope"}
    for code in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503)
}


class ContractAPIRouter(APIRouter):
    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        **kwargs: Any,
    ) -> None:
        response_model = kwargs.get("response_model")
        if path.startswith("/v1/") and not _returns_raw_response(endpoint):
            if isinstance(response_model, DefaultPlaceholder):
                kwargs["response_model"] = PortraitSuccessResponse
            responses = dict(kwargs.get("responses") or {})
            for code, definition in _ERROR_RESPONSES.items():
                responses.setdefault(code, definition)
            kwargs["responses"] = responses
        super().add_api_route(path, endpoint, **kwargs)


__all__ = [
    "AnalysisDetailResponse",
    "AnalysisListResponse",
    "ContractAPIRouter",
    "InferAppearanceResponse",
    "InferFacesResponse",
    "InferGaitResponse",
    "InferPersonsResponse",
    "InferPoseResponse",
    "InferTracksResponse",
    "PortraitErrorResponse",
    "PortraitSuccess",
    "PortraitSuccessResponse",
    "VisionInferResponse",
]
