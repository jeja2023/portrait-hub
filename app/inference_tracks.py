from typing import Any

from PIL import Image

from app.inference_detection import infer_person_frames
from app.inference_reid import infer_reid_images
from app.metrics import observe
from app.model_package import get_model_path
from app.model_refs import cache_key
from app.portrait_tracking import associate_person_tracks
from app.runtime import get_or_load_model, touch_model
from app.vision import crop_person, person_crop_quality


def timing_payload(meta: dict[str, Any]) -> dict[str, Any]:
    timing = meta.get("timing")
    return timing if isinstance(timing, dict) else {}


def merge_person_quality(person: dict[str, Any], crop_quality: dict[str, Any]) -> None:
    raw_existing = person.get("quality")
    existing = raw_existing if isinstance(raw_existing, dict) else {}
    detection_score = max(0.0, min(1.0, float(person.get("score", 0.0))))
    crop_score = max(0.0, min(1.0, float(crop_quality.get("score", 0.0))))
    existing_score = max(0.0, min(1.0, float(existing.get("score", detection_score))))
    fused_score = crop_score * 0.58 + existing_score * 0.22 + detection_score * 0.20
    person["crop_quality"] = crop_quality
    person["quality"] = {
        **existing,
        "score": round(fused_score, 6),
        "crop_score": round(crop_score, 6),
        "detection_score": round(detection_score, 6),
        "source": "crop_detection_fusion",
        "usable": bool(crop_quality.get("usable", False)),
    }


async def infer_tracks_for_images(
    images: list[Image.Image],
    filenames: list[str | None],
    detector_project_name: str,
    detector_model_name: str,
    reid_project_name: str,
    reid_model_name: str,
    confidence: float,
    iou: float,
    max_detections: int,
    include_embeddings: bool,
) -> dict[str, Any]:
    detector_key = cache_key(detector_project_name, detector_model_name)
    reid_key = cache_key(reid_project_name, reid_model_name)

    detector_bundle, detector_cold_loaded, detector_load_seconds = await get_or_load_model(
        detector_key,
        get_model_path(detector_project_name, detector_model_name),
    )
    reid_bundle, reid_cold_loaded, reid_load_seconds = await get_or_load_model(
        reid_key,
        get_model_path(reid_project_name, reid_model_name),
    )

    frames, detector_meta = await infer_person_frames(
        detector_bundle,
        detector_key,
        images,
        filenames,
        confidence=confidence,
        iou=iou,
        max_detections=max_detections,
    )

    crops: list[Image.Image] = []
    crop_refs: list[tuple[int, int]] = []
    for frame in frames:
        image = images[frame["frame_index"]]
        for person_index, person in enumerate(frame["persons"]):
            crop_quality = person_crop_quality(image, person["box"])
            merge_person_quality(person, crop_quality)
            crop = crop_person(image, person["box"])
            if crop is not None:
                crops.append(crop)
                crop_refs.append((frame["frame_index"], person_index))

    embedding_count = 0
    if crops:
        embeddings, embedding_meta = await infer_reid_images(reid_bundle, reid_key, crops)
        embedding_count = embeddings.shape[0]
        for index, (frame_index, person_index) in enumerate(crop_refs):
            person = frames[frame_index]["persons"][person_index]
            person["embedding_dim"] = int(embeddings.shape[1])
            person["embedding_index"] = index
            tracking_embedding = [round(float(value), 8) for value in embeddings[index].tolist()]
            person["_tracking_embedding"] = tracking_embedding
            if include_embeddings:
                person["embedding"] = tracking_embedding
    else:
        embedding_meta = {
            "input_shape": [0],
            "output_shapes": [],
            "inference_mode": "none",
            "embedding_dim": 0,
            "timing": {
                "preprocess_seconds": 0,
                "queue_seconds": 0,
                "inference_seconds": 0,
                "postprocess_seconds": 0,
            },
        }

    tracking_meta = associate_person_tracks(frames, include_template_embeddings=include_embeddings)
    person_count = sum(frame["person_count"] for frame in frames)
    await touch_model(detector_key, detector_bundle)
    await touch_model(reid_key, reid_bundle)
    observe("persons_detected_total", person_count)
    observe("persons_frames_total", len(frames))
    detector_timing = timing_payload(detector_meta)
    embedding_timing = timing_payload(embedding_meta)
    observe("preprocess_seconds_sum", float(detector_timing.get("preprocess_seconds", 0.0)) + float(embedding_timing.get("preprocess_seconds", 0.0)))
    observe("postprocess_seconds_sum", float(detector_timing.get("postprocess_seconds", 0.0)) + float(embedding_timing.get("postprocess_seconds", 0.0)))

    return {
        "detector_key": detector_key,
        "reid_key": reid_key,
        "detector_cold_loaded": detector_cold_loaded,
        "reid_cold_loaded": reid_cold_loaded,
        "detector_load_seconds": detector_load_seconds,
        "reid_load_seconds": reid_load_seconds,
        "detector_meta": detector_meta,
        "embedding_meta": embedding_meta,
        "frames": frames,
        "tracks": tracking_meta["tracks"],
        "track_count": tracking_meta["track_count"],
        "tracker": {key: value for key, value in tracking_meta.items() if key != "tracks"},
        "person_count": person_count,
        "embedding_count": embedding_count,
    }


__all__ = [
    "merge_person_quality",
    "infer_tracks_for_images",
]
