"""遗留路由模块的兼容性外观门面。

此模块故意只重新导出较小的显式接口。对于新编写的代码，路由模块应优先使用直接导入的方式导入相关拥有的模块。
"""

from app.constants import COCO_CLASSES
from app.image_io import load_images
from app.inference import infer_classification_images, infer_detection_images, infer_person_frames, infer_reid_images
from app.inference_tracks import infer_tracks_for_images
from app.media.stream_decode import validate_media_stream_url
from app.metrics import observe, prometheus_metrics
from app.model_config import (
    MODEL_ALIASES,
    MODEL_CONFIGS,
    alias_resolution,
    model_config,
    model_task,
    reload_model_config_state,
    resolve_model_reference,
)
from app.model_config_access import configured_input_size
from app.model_package import get_model_path, model_hash, model_package_info, public_model_config
from app.model_refs import cache_key, split_cache_key, validate_alias_name, validate_model_reference_parts
from app.observability import (
    log_json,
    logger,
    now,
    request_id_from_headers,
    reset_log_context,
    set_log_context,
    traceparent_from_headers,
    wall_time,
)
from app.runtime import (
    MODEL_LOAD_LOCKS,
    MODEL_REGISTRY,
    build_input_array,
    bundle_info,
    get_or_load_model,
    input_dtype,
    run_model_bundle,
    touch_model,
    unload_model_by_key,
)
from app.schemas import (
    AliasRollbackRequest,
    AliasSwitchRequest,
    AliasWeightedRolloutRequest,
    InferenceRequest,
    ModelRequest,
    WarmupRequest,
)
from app.security import require_api_token
from app.settings import (
    ALLOW_STREAM_URLS,
    MAX_EMBEDDING_IMAGES,
    MAX_LOADED_MODELS,
    MAX_PERSON_FRAMES,
    MAX_PIPELINE_FRAMES,
    MAX_VIDEO_FRAME_UPLOADS,
    MAX_VISION_IMAGES,
    STREAM_INFERENCE_BATCH_SIZE,
    STREAM_READ_TIMEOUT_SECONDS,
    STREAM_SAMPLE_INTERVAL_SECONDS,
    VIDEO_INFERENCE_BATCH_SIZE,
    VIDEO_SAMPLE_INTERVAL_SECONDS,
    WARMUP_MODELS,
)
from app.video_io import extract_video_frames_from_path, extract_video_frames_from_upload, validate_stream_url
from app.vision import crop_person, letterbox_image, person_crop_quality, resize_image_tensor

__all__ = [
    "ALLOW_STREAM_URLS",
    "COCO_CLASSES",
    "MAX_EMBEDDING_IMAGES",
    "MAX_LOADED_MODELS",
    "MAX_PERSON_FRAMES",
    "MAX_PIPELINE_FRAMES",
    "MAX_VIDEO_FRAME_UPLOADS",
    "MAX_VISION_IMAGES",
    "MODEL_ALIASES",
    "MODEL_CONFIGS",
    "MODEL_LOAD_LOCKS",
    "MODEL_REGISTRY",
    "STREAM_INFERENCE_BATCH_SIZE",
    "STREAM_READ_TIMEOUT_SECONDS",
    "STREAM_SAMPLE_INTERVAL_SECONDS",
    "VIDEO_INFERENCE_BATCH_SIZE",
    "VIDEO_SAMPLE_INTERVAL_SECONDS",
    "WARMUP_MODELS",
    "AliasRollbackRequest",
    "AliasSwitchRequest",
    "AliasWeightedRolloutRequest",
    "InferenceRequest",
    "ModelRequest",
    "WarmupRequest",
    "alias_resolution",
    "build_input_array",
    "bundle_info",
    "cache_key",
    "configured_input_size",
    "crop_person",
    "extract_video_frames_from_path",
    "extract_video_frames_from_upload",
    "get_model_path",
    "get_or_load_model",
    "infer_classification_images",
    "infer_detection_images",
    "infer_person_frames",
    "infer_reid_images",
    "infer_tracks_for_images",
    "input_dtype",
    "letterbox_image",
    "load_images",
    "log_json",
    "logger",
    "model_config",
    "model_hash",
    "model_package_info",
    "model_task",
    "now",
    "observe",
    "person_crop_quality",
    "prometheus_metrics",
    "public_model_config",
    "reload_model_config_state",
    "request_id_from_headers",
    "require_api_token",
    "reset_log_context",
    "resize_image_tensor",
    "resolve_model_reference",
    "run_model_bundle",
    "set_log_context",
    "split_cache_key",
    "touch_model",
    "traceparent_from_headers",
    "unload_model_by_key",
    "validate_alias_name",
    "validate_media_stream_url",
    "validate_model_reference_parts",
    "validate_stream_url",
    "wall_time",
]
