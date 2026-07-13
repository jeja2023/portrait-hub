import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from PIL import Image

from app.model_config import config_value, configured_input_size, model_config
from app.model_package import labels_from_config, parse_class_filter
from app.observability import now
from app.runtime import run_yolo_frames
from app.schemas import LetterboxMeta, ModelBundle
from app.vision import letterbox_image, yolo_detections, yolo_person_detections

Array = npt.NDArray[Any]


def _letterbox_batch(
    images: list[Image.Image],
    input_height: int,
    input_width: int,
) -> tuple[Array, list[LetterboxMeta]]:
    # 在单个工作线程里对整批做 letterbox（一次 asyncio.to_thread 跳转），而不是每张图
    # 每张图都 await 一次跳转——后者会把 resize 串行化。
    tensors: list[Array] = []
    metas: list[LetterboxMeta] = []
    for image in images:
        tensor, meta = letterbox_image(image, input_height, input_width)
        tensors.append(tensor)
        metas.append(meta)
    return np.stack(tensors, axis=0), metas


async def infer_person_frames(
    bundle: ModelBundle,
    key: str,
    images: list[Image.Image],
    filenames: list[str | None],
    confidence: float,
    iou: float,
    max_detections: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = bundle["session"]
    config = model_config(key, default_type="yolo")
    input_height, input_width = configured_input_size(key, session, default=(640, 640))
    person_class_id = int(config.get("person_class_id", 0))

    preprocess_start = now()
    input_array, image_metas = await asyncio.to_thread(_letterbox_batch, images, input_height, input_width)
    preprocess_seconds = now() - preprocess_start

    raw_outputs, queue_seconds, inference_seconds, inference_mode = await run_yolo_frames(bundle, input_array)

    postprocess_start = now()
    frames: list[dict[str, Any]] = []
    output_shapes = [list(output.shape) for output in raw_outputs]
    for index, meta in enumerate(image_metas):
        frame_outputs = []
        for output in raw_outputs:
            if output.ndim > 0 and output.shape[0] == len(image_metas):
                frame_outputs.append(output[index : index + 1])
            else:
                frame_outputs.append(output)
        persons = yolo_person_detections(
            frame_outputs,
            meta,
            confidence_threshold=confidence,
            iou_threshold=iou,
            max_detections=max_detections,
            person_class_id=person_class_id,
        )
        frames.append(
            {
                "frame_index": index,
                "width": meta["original_width"],
                "height": meta["original_height"],
                "persons": persons,
                "person_count": len(persons),
            }
        )
    postprocess_seconds = now() - postprocess_start

    timing = {
        "preprocess_seconds": preprocess_seconds,
        "queue_seconds": queue_seconds,
        "inference_seconds": inference_seconds,
        "postprocess_seconds": postprocess_seconds,
    }
    runtime_meta: dict[str, Any] = {
        "input_shape": list(input_array.shape),
        "output_shapes": output_shapes,
        "inference_mode": inference_mode,
        "timing": timing,
    }
    return frames, runtime_meta
async def infer_detection_images(
    bundle: ModelBundle,
    key: str,
    images: list[Image.Image],
    filenames: list[str | None],
    confidence: float | None = None,
    iou: float | None = None,
    max_detections: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = bundle["session"]
    config = model_config(key, default_type="yolo")
    model_path = Path(bundle["path"])
    input_height, input_width = configured_input_size(key, session, default=(640, 640))
    labels = labels_from_config(config, model_path)
    confidence_value = float(confidence if confidence is not None else config_value(config, "output", "confidence", 0.25))
    iou_value = float(iou if iou is not None else config_value(config, "output", "iou", 0.45))
    max_detections_value = int(max_detections if max_detections is not None else config_value(config, "output", "max_detections", 100))
    class_filter_ids = parse_class_filter(config_value(config, "output", "class_filter"), labels)

    preprocess_start = now()
    input_array, image_metas = await asyncio.to_thread(_letterbox_batch, images, input_height, input_width)
    preprocess_seconds = now() - preprocess_start

    raw_outputs, queue_seconds, inference_seconds, inference_mode = await run_yolo_frames(bundle, input_array)

    postprocess_start = now()
    frames: list[dict[str, Any]] = []
    output_shapes = [list(output.shape) for output in raw_outputs]
    for index, meta in enumerate(image_metas):
        frame_outputs = []
        for output in raw_outputs:
            if output.ndim > 0 and output.shape[0] == len(image_metas):
                frame_outputs.append(output[index : index + 1])
            else:
                frame_outputs.append(output)
        detections = yolo_detections(
            frame_outputs,
            meta,
            confidence_threshold=confidence_value,
            iou_threshold=iou_value,
            max_detections=max_detections_value,
            class_filter_ids=class_filter_ids,
            labels=labels,
        )
        frames.append(
            {
                "image_index": index,
                "width": meta["original_width"],
                "height": meta["original_height"],
                "detections": detections,
                "detection_count": len(detections),
            }
        )
    postprocess_seconds = now() - postprocess_start

    runtime_meta: dict[str, Any] = {
        "input_shape": list(input_array.shape),
        "output_shapes": output_shapes,
        "inference_mode": inference_mode,
        "labels_count": len(labels),
        "timing": {
            "preprocess_seconds": preprocess_seconds,
            "queue_seconds": queue_seconds,
            "inference_seconds": inference_seconds,
            "postprocess_seconds": postprocess_seconds,
        },
        "parameters": {
            "confidence": confidence_value,
            "iou": iou_value,
            "max_detections": max_detections_value,
            "class_filter": sorted(class_filter_ids) if class_filter_ids else None,
        },
    }
    return frames, runtime_meta


__all__ = [
    "infer_person_frames",
    "infer_detection_images",
]
