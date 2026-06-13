from copy import deepcopy
import math
from typing import Any

from fastapi import HTTPException, status

from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import PORTRAIT_STORAGE_BACKEND, PORTRAIT_THRESHOLDS_STATE_PATH


DEFAULT_THRESHOLD_PROFILES: dict[str, dict[str, dict[str, float]]] = {
    "face": {"strict": 0.82, "normal": 0.76, "loose": 0.70},
    "body": {"strict": 0.74, "normal": 0.68, "loose": 0.62},
    "gait": {"strict": 0.70, "normal": 0.64, "loose": 0.58},
    "appearance": {"strict": 0.68, "normal": 0.58, "loose": 0.48},
    "fusion": {"strict": 0.80, "normal": 0.72, "loose": 0.64},
}

THRESHOLD_PROFILES = deepcopy(DEFAULT_THRESHOLD_PROFILES)
SUPPORTED_THRESHOLD_PROFILES = frozenset({"strict", "normal", "loose"})


def load_threshold_state() -> None:
    if PORTRAIT_STORAGE_BACKEND == "postgres":
        from app.portrait_postgres import load_threshold_snapshot

        payload = load_threshold_snapshot()
    else:
        payload = read_json_state(PORTRAIT_THRESHOLDS_STATE_PATH, {})
    if not isinstance(payload, dict):
        handle_state_read_error(f"threshold state root must be a mapping: {PORTRAIT_THRESHOLDS_STATE_PATH}")
        return
    thresholds = payload.get("thresholds", payload)
    if not isinstance(thresholds, dict):
        handle_state_read_error(f"threshold state thresholds must be a mapping: {PORTRAIT_THRESHOLDS_STATE_PATH}")
        return
    for modality, profiles in thresholds.items():
        modality_key = normalize_modality(str(modality))
        if modality_key not in THRESHOLD_PROFILES or not isinstance(profiles, dict):
            continue
        for profile, raw_value in profiles.items():
            if profile not in THRESHOLD_PROFILES[modality_key]:
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if 0.0 <= value <= 1.0:
                THRESHOLD_PROFILES[modality_key][profile] = value


def save_threshold_state() -> None:
    payload = {"version": 1, "thresholds": threshold_snapshot()}
    if PORTRAIT_STORAGE_BACKEND == "postgres":
        from app.portrait_postgres import save_threshold_snapshot

        save_threshold_snapshot(payload["thresholds"])
        return
    write_json_state(PORTRAIT_THRESHOLDS_STATE_PATH, payload)


def threshold_snapshot() -> dict[str, Any]:
    return deepcopy(THRESHOLD_PROFILES)


def get_threshold(modality: str, profile: str = "normal") -> float:
    modality_key = validate_threshold_modality(modality)
    thresholds = THRESHOLD_PROFILES.get(modality_key)
    profile_key = validate_threshold_profile(profile)
    return float(thresholds[profile_key])


def normalize_modality(modality: str) -> str:
    value = modality.strip().lower()
    if value in {"person", "persons", "person_reid", "reid"}:
        return "body"
    if value in {"clothing", "clothes"}:
        return "appearance"
    return value


def validate_threshold_profile(profile: str) -> str:
    value = str(profile).strip().lower()
    if value not in SUPPORTED_THRESHOLD_PROFILES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported threshold profile")
    return value


def validate_threshold_modality(modality: str) -> str:
    modality_key = normalize_modality(str(modality))
    if modality_key not in THRESHOLD_PROFILES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported modality")
    return modality_key


def validate_threshold_value(modality: str, raw_value: Any) -> float:
    if isinstance(raw_value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"threshold for {modality} must be numeric")
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"threshold for {modality} must be numeric",
        ) from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"threshold for {modality} must be between 0 and 1",
        )
    return value


def update_threshold_profile(profile: str, payload: dict[str, Any]) -> dict[str, Any]:
    profile_key = validate_threshold_profile(profile)
    previous_thresholds = threshold_snapshot()

    updated: dict[str, float] = {}
    try:
        for modality, raw_value in payload.items():
            modality_key = validate_threshold_modality(modality)
            value = validate_threshold_value(str(modality), raw_value)
            THRESHOLD_PROFILES[modality_key][profile_key] = value
            updated[modality_key] = value
        save_threshold_state()
    except Exception:
        THRESHOLD_PROFILES.clear()
        THRESHOLD_PROFILES.update(deepcopy(previous_thresholds))
        raise
    return {"profile": profile_key, "updated": updated, "thresholds": threshold_snapshot()}


load_threshold_state()
