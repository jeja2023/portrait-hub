from copy import deepcopy
from typing import Any

import yaml

from app.settings import MODEL_CAPABILITIES_PATH, PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES


DEFAULT_CAPABILITIES = {
    "face_detection": {
        "status": "fallback",
        "model_id": "opencv/haarcascade_frontalface_default",
        "adapter": "haar_face_detection",
        "production_adapter": "scrfd",
        "fallback_model_id": "opencv/haarcascade_frontalface_default",
    },
    "face_embedding": {
        "status": "fallback",
        "model_id": "portrait_hub/image_fingerprint_v1",
        "adapter": "image_fingerprint",
        "production_adapter": "arcface",
        "embedding_dim": 64,
        "production_embedding_dim": 512,
        "normalize": "l2",
        "fallback_model_id": "portrait_hub/image_fingerprint_v1",
    },
    "pose": {
        "status": "placeholder",
        "model_id": "portrait_hub/geometric_pose_placeholder",
        "adapter": "geometric_pose_placeholder",
        "production_adapter": "rtmpose",
        "keypoint_schema": "coco17",
        "fallback_model_id": "portrait_hub/geometric_pose_placeholder",
    },
    "person_detection": {
        "status": "fallback",
        "model_id": "portrait_hub/whole_image_person_fallback",
        "adapter": "whole_image_person",
        "production_adapter": "yolo",
        "input_size": [640, 640],
        "confidence": 0.25,
        "iou": 0.45,
        "max_detections": 8,
        "fallback_model_id": "portrait_hub/whole_image_person_fallback",
    },
    "gait": {
        "status": "fallback",
        "model_id": "portrait_hub/tracklet_fingerprint_v1",
        "adapter": "tracklet_fingerprint",
        "production_adapter": "opengait",
        "embedding_dim": 64,
        "production_embedding_dim": 256,
        "normalize": "l2",
        "fallback_model_id": "portrait_hub/tracklet_fingerprint_v1",
    },
    "body_embedding": {
        "status": "fallback",
        "model_id": "portrait_hub/image_fingerprint_v1",
        "adapter": "image_fingerprint",
        "production_adapter": "reid",
        "embedding_dim": 64,
        "production_embedding_dim": 512,
        "normalize": "l2",
        "fallback_model_id": "portrait_hub/image_fingerprint_v1",
    },
    "appearance": {
        "status": "fallback",
        "model_id": "portrait_hub/color_histogram_v1",
        "adapter": "color_histogram",
        "production_adapter": "attribute_reid",
        "embedding_dim": 64,
        "production_embedding_dim": 256,
        "normalize": "l2",
        "fallback_model_id": "portrait_hub/color_histogram_v1",
    },
}


ADAPTER_SCHEMAS = {
    "scrfd": {
        "task": "face_detection",
        "input_size": [640, 640],
        "output": "boxes_landmarks_scores",
        "embedding_dim": 0,
    },
    "arcface": {
        "task": "face_embedding",
        "input_size": [112, 112],
        "embedding_dim": 512,
        "normalize": "l2",
        "preprocess": "rgb_minus_127p5_div_128",
    },
    "rtmpose": {
        "task": "pose",
        "input_size": [256, 192],
        "keypoint_schema": "coco17",
        "embedding_dim": 0,
    },
    "opengait": {
        "task": "gait",
        "input_size": [64, 44],
        "embedding_dim": 256,
        "normalize": "l2",
        "sequence_input": True,
    },
    "reid": {
        "task": "body_embedding",
        "input_size": [256, 128],
        "embedding_dim": 512,
        "normalize": "l2",
        "preprocess": "imagenet",
    },
    "attribute_reid": {
        "task": "appearance",
        "input_size": [256, 128],
        "embedding_dim": 256,
        "normalize": "l2",
        "preprocess": "imagenet",
    },
    "yolo": {
        "task": "person_detection",
        "input_size": [640, 640],
        "embedding_dim": 0,
    },
}


def normalize_capabilities(raw: dict[str, Any]) -> dict[str, Any]:
    capabilities: dict[str, Any] = deepcopy(DEFAULT_CAPABILITIES)
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue
        merged = {**capabilities.get(name, {}), **value}
        adapter = str(merged.get("adapter") or merged.get("production_adapter") or "")
        if adapter in ADAPTER_SCHEMAS:
            schema = ADAPTER_SCHEMAS[adapter]
            merged = {**schema, **merged}
            if value.get("embedding_dim") is None and schema.get("embedding_dim") is not None:
                merged["embedding_dim"] = schema["embedding_dim"]
            if value.get("input_size") is None and schema.get("input_size") is not None:
                merged["input_size"] = schema["input_size"]
        production_adapter = str(merged.get("production_adapter") or "")
        if production_adapter in ADAPTER_SCHEMAS:
            schema = ADAPTER_SCHEMAS[production_adapter]
            merged.setdefault("production_embedding_dim", schema.get("embedding_dim"))
            merged.setdefault("input_size", schema.get("input_size"))
            merged.setdefault("normalize", schema.get("normalize"))
            merged.setdefault("keypoint_schema", schema.get("keypoint_schema"))
        capabilities[name] = merged
    return capabilities


def load_capabilities() -> dict[str, Any]:
    if MODEL_CAPABILITIES_PATH.exists():
        with MODEL_CAPABILITIES_PATH.open("r", encoding="utf-8") as file:
            payload = yaml.safe_load(file) or {}
        if isinstance(payload, dict):
            raw = payload.get("capabilities", payload)
            if isinstance(raw, dict):
                return normalize_capabilities(raw)
    return deepcopy(DEFAULT_CAPABILITIES)


MODEL_CAPABILITIES = load_capabilities()


def reload_model_capabilities() -> dict[str, Any]:
    MODEL_CAPABILITIES.clear()
    MODEL_CAPABILITIES.update(load_capabilities())
    validate_required_production_capabilities(MODEL_CAPABILITIES)
    return MODEL_CAPABILITIES


def capability_status(name: str) -> dict[str, Any]:
    capability = MODEL_CAPABILITIES.get(name)
    return capability if isinstance(capability, dict) else {"status": "not_configured"}


def embedding_dimension_for_modality(modality: str) -> int:
    capability_name = {
        "face": "face_embedding",
        "gait": "gait",
        "appearance": "appearance",
        "body": "body_embedding",
    }.get(str(modality), str(modality))
    capability = capability_status(capability_name)
    try:
        return int(capability.get("embedding_dim") or capability.get("production_embedding_dim") or 0)
    except (TypeError, ValueError):
        return 0


def production_model_ready(name: str) -> bool:
    capability = capability_status(name)
    return capability.get("status") in {"ready", "production"} and capability.get("model_id") != capability.get("fallback_model_id")


def non_production_capability_names(capabilities: dict[str, Any] | None = None) -> list[str]:
    current = capabilities if capabilities is not None else MODEL_CAPABILITIES
    names: list[str] = []
    for name, capability in current.items():
        if not isinstance(capability, dict):
            names.append(str(name))
            continue
        if capability.get("status") not in {"ready", "production"} or capability.get("model_id") == capability.get("fallback_model_id"):
            names.append(str(name))
    return sorted(names)


def validate_required_production_capabilities(capabilities: dict[str, Any] | None = None) -> None:
    if not PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES:
        return
    missing = non_production_capability_names(capabilities)
    if missing:
        raise RuntimeError(
            "production model capabilities are required but not ready: "
            + ", ".join(missing)
        )


validate_required_production_capabilities(MODEL_CAPABILITIES)
