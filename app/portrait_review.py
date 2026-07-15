from __future__ import annotations

import copy
import hashlib
import secrets
import threading
from collections import Counter
from typing import Any

from fastapi import HTTPException, status

from app.observability import wall_time
from app.portrait_state import read_json_state, write_json_state
from app.settings import PORTRAIT_REVIEW_STATE_PATH

_REVIEW_LOCK = threading.RLock()
_REVIEW_STATE: dict[str, list[dict[str, Any]]] = {"annotations": []}
_REVIEW_LABELS = {"false_positive", "mismatch", "low_quality", "confirmed", "uncertain"}
MAX_REVIEW_ANNOTATIONS = 10_000
MAX_REVIEW_LIST_LIMIT = 500
MAX_REVIEW_DATASET_LIMIT = 100
MAX_REVIEW_TEXT_LENGTH = 512
MAX_REVIEW_NOTE_LENGTH = 2000
_REVIEW_ATTENTION_LABELS = {"false_positive", "mismatch", "low_quality", "uncertain"}


def review_state_payload() -> dict[str, list[dict[str, Any]]]:
    with _REVIEW_LOCK:
        return copy.deepcopy(_REVIEW_STATE)


def restore_review_state(snapshot: dict[str, list[dict[str, Any]]]) -> None:
    with _REVIEW_LOCK:
        _REVIEW_STATE["annotations"] = copy.deepcopy(snapshot.get("annotations", []))
        save_review_state()


def save_review_state() -> None:
    write_json_state(PORTRAIT_REVIEW_STATE_PATH, review_state_payload())


def load_review_state() -> None:
    payload = read_json_state(PORTRAIT_REVIEW_STATE_PATH, {"annotations": []})
    if not isinstance(payload, dict):
        payload = {"annotations": []}
    annotations = payload.get("annotations", [])
    with _REVIEW_LOCK:
        _REVIEW_STATE["annotations"] = [annotation for annotation in annotations if isinstance(annotation, dict)]


def clear_review_state() -> None:
    with _REVIEW_LOCK:
        _REVIEW_STATE["annotations"] = []


def bounded_text(value: Any, field_name: str, *, required: bool = False, max_length: int = MAX_REVIEW_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 为必填项")
    if len(text) > max_length:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 过长")
    return text


def normalize_review_label(value: Any) -> str:
    label = bounded_text(value, "label", required=True, max_length=64)
    if label not in _REVIEW_LABELS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的审阅标签")
    return label


def public_review_annotation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "annotation_id": record.get("annotation_id"),
        "tenant_id": record.get("tenant_id"),
        "job_id": record.get("job_id"),
        "track_id": record.get("track_id"),
        "label": record.get("label"),
        "reviewer": record.get("reviewer"),
        "note": record.get("note"),
        "frame_index": record.get("frame_index"),
        "evidence_ref": record.get("evidence_ref"),
        "created_at": record.get("created_at"),
        "source": record.get("source"),
    }


def _created_at_sort_value(row: dict[str, Any]) -> float:
    try:
        return float(row.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _matching_review_rows(
    tenant_id: str,
    *,
    job_id: str | None = None,
    track_id: str | None = None,
    label: str | None = None,
) -> list[dict[str, Any]]:
    normalized_label = normalize_review_label(label) if label else None
    rows: list[dict[str, Any]] = []
    with _REVIEW_LOCK:
        for record in _REVIEW_STATE["annotations"]:
            if record.get("tenant_id") != tenant_id:
                continue
            if job_id and record.get("job_id") != job_id:
                continue
            if track_id and record.get("track_id") != track_id:
                continue
            if normalized_label and record.get("label") != normalized_label:
                continue
            rows.append(public_review_annotation(record))
    rows.sort(key=_created_at_sort_value, reverse=True)
    return rows


def _top_counts(counter: Counter[str], key_name: str, *, limit: int = 10) -> list[dict[str, Any]]:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [{key_name: name, "count": count} for name, count in ranked[:limit]]


def list_review_annotations(
    tenant_id: str,
    *,
    job_id: str | None = None,
    track_id: str | None = None,
    label: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), MAX_REVIEW_LIST_LIMIT))
    return _matching_review_rows(tenant_id, job_id=job_id, track_id=track_id, label=label)[:bounded_limit]


def review_annotation_summary(
    tenant_id: str,
    *,
    job_id: str | None = None,
    track_id: str | None = None,
    label: str | None = None,
    recent_limit: int = 10,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(recent_limit), MAX_REVIEW_LIST_LIMIT))
    rows = _matching_review_rows(tenant_id, job_id=job_id, track_id=track_id, label=label)
    label_counts = Counter(str(row.get("label") or "unknown") for row in rows)
    job_counts = Counter(str(row.get("job_id")) for row in rows if row.get("job_id"))
    track_counts = Counter(str(row.get("track_id")) for row in rows if row.get("track_id"))
    evidence_index = [
        {
            "job_id": row.get("job_id"),
            "track_id": row.get("track_id"),
            "label": row.get("label"),
            "frame_index": row.get("frame_index"),
            "evidence_ref": row.get("evidence_ref"),
            "created_at": row.get("created_at"),
        }
        for row in rows
        if row.get("evidence_ref")
    ][:bounded_limit]
    return {
        "count": len(rows),
        "total_annotations": len(rows),
        "unique_job_count": len(job_counts),
        "unique_track_count": len(track_counts),
        "review_attention_count": sum(label_counts.get(item, 0) for item in _REVIEW_ATTENTION_LABELS),
        "label_counts": _top_counts(label_counts, "label"),
        "job_counts": _top_counts(job_counts, "job_id"),
        "track_counts": _top_counts(track_counts, "track_id"),
        "recent_annotations": rows[:bounded_limit],
        "evidence_index": evidence_index,
        "filters": {"job_id": job_id, "track_id": track_id, "label": label},
    }

def _dataset_id(tenant_id: str, name: str, rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(tenant_id.encode("utf-8"))
    digest.update(name.encode("utf-8"))
    for row in rows:
        digest.update(str(row.get("annotation_id") or "").encode("utf-8"))
        digest.update(str(row.get("created_at") or "").encode("utf-8"))
    return f"eval_{digest.hexdigest()[:16]}"


def _dataset_evidence(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    evidence = []
    for row in rows:
        if not row.get("evidence_ref"):
            continue
        evidence.append(
            {
                "job_id": row.get("job_id"),
                "track_id": row.get("track_id"),
                "label": row.get("label"),
                "frame_index": row.get("frame_index"),
                "evidence_ref": row.get("evidence_ref"),
                "created_at": row.get("created_at"),
            }
        )
        if len(evidence) >= limit:
            break
    return evidence


def _dataset_record(
    tenant_id: str,
    name: str,
    purpose: str,
    rows: list[dict[str, Any]],
    *,
    evidence_limit: int,
) -> dict[str, Any]:
    label_counts = Counter(str(row.get("label") or "unknown") for row in rows)
    job_ids = {str(row.get("job_id")) for row in rows if row.get("job_id")}
    track_ids = {str(row.get("track_id")) for row in rows if row.get("track_id")}
    return {
        "dataset_id": _dataset_id(tenant_id, name, rows),
        "name": name,
        "purpose": purpose,
        "sample_count": len(rows),
        "job_count": len(job_ids),
        "track_count": len(track_ids),
        "label_counts": _top_counts(label_counts, "label"),
        "latest_created_at": rows[0].get("created_at") if rows else None,
        "evidence_index": _dataset_evidence(rows, limit=evidence_limit),
    }


def list_review_datasets(
    tenant_id: str,
    *,
    limit: int = MAX_REVIEW_DATASET_LIMIT,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), MAX_REVIEW_DATASET_LIMIT))
    rows = _matching_review_rows(tenant_id)
    attention_rows = [row for row in rows if row.get("label") in _REVIEW_ATTENTION_LABELS]
    confirmed_rows = [row for row in rows if row.get("label") == "confirmed"]
    low_quality_rows = [row for row in rows if row.get("label") == "low_quality"]
    mismatch_rows = [row for row in rows if row.get("label") in {"mismatch", "false_positive"}]
    candidates = [
        _dataset_record(tenant_id, "review_all_annotations", "manual_review_pool", rows, evidence_limit=bounded_limit),
        _dataset_record(tenant_id, "review_attention_holdout", "regression_holdout", attention_rows, evidence_limit=bounded_limit),
        _dataset_record(tenant_id, "review_confirmed_samples", "positive_control", confirmed_rows, evidence_limit=bounded_limit),
        _dataset_record(tenant_id, "review_low_quality_samples", "quality_calibration", low_quality_rows, evidence_limit=bounded_limit),
        _dataset_record(tenant_id, "review_mismatch_samples", "association_regression", mismatch_rows, evidence_limit=bounded_limit),
    ]
    return [dataset for dataset in candidates if dataset["sample_count"] > 0][:bounded_limit]



def _clamp_threshold(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _threshold_recommendation_record(
    modality: str,
    profile: str,
    current: float,
    recommended: float,
    action: str,
    reason: str,
    evidence_counts: dict[str, int],
    confidence: str,
) -> dict[str, Any]:
    recommended_value = _clamp_threshold(recommended)
    current_value = round(float(current), 4)
    return {
        "modality": modality,
        "profile": profile,
        "current_threshold": current_value,
        "recommended_threshold": recommended_value,
        "delta": round(recommended_value - current_value, 4),
        "action": action,
        "reason": reason,
        "confidence": confidence,
        "evidence_counts": evidence_counts,
        "auto_apply": False,
    }


def review_threshold_recommendations(
    tenant_id: str,
    *,
    thresholds: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    rows = _matching_review_rows(tenant_id)
    label_counts = Counter(str(row.get("label") or "unknown") for row in rows)
    evidence_counts = {label: int(label_counts.get(label, 0)) for label in sorted(_REVIEW_LABELS)}
    total = len(rows)
    attention_count = sum(label_counts.get(label, 0) for label in _REVIEW_ATTENTION_LABELS)
    mismatch_count = label_counts.get("false_positive", 0) + label_counts.get("mismatch", 0)
    confirmed_count = label_counts.get("confirmed", 0)
    low_quality_count = label_counts.get("low_quality", 0)
    threshold_map = thresholds or {}
    body_normal = float(threshold_map.get("body", {}).get("normal", 0.68))
    fusion_normal = float(threshold_map.get("fusion", {}).get("normal", 0.72))
    recommendations: list[dict[str, Any]] = []
    if total < 3:
        action = "collect_more_samples"
        reason = "已复核样本少于 3 个；保持阈值不变，等待更多证据"
        confidence = "low"
        body_target = body_normal
        fusion_target = fusion_normal
    elif mismatch_count > confirmed_count:
        action = "raise_threshold"
        reason = "false positive or mismatch annotations outnumber confirmed samples"
        confidence = "medium" if total >= 5 else "low"
        body_target = body_normal + 0.02
        fusion_target = fusion_normal + 0.015
    elif confirmed_count >= attention_count * 2 and confirmed_count >= 3:
        action = "hold_threshold"
        reason = "confirmed samples dominate current review pool"
        confidence = "medium"
        body_target = body_normal
        fusion_target = fusion_normal
    else:
        action = "hold_threshold"
        reason = "复核证据不一致；保持阈值不变并继续收集样本"
        confidence = "low"
        body_target = body_normal
        fusion_target = fusion_normal
    recommendations.append(
        _threshold_recommendation_record(
            "body",
            "normal",
            body_normal,
            body_target,
            action,
            reason,
            evidence_counts,
            confidence,
        )
    )
    recommendations.append(
        _threshold_recommendation_record(
            "fusion",
            "normal",
            fusion_normal,
            fusion_target,
            action,
            reason,
            evidence_counts,
            confidence,
        )
    )
    if low_quality_count:
        quality_current = float(threshold_map.get("appearance", {}).get("normal", 0.58))
        recommendations.append(
            _threshold_recommendation_record(
                "appearance",
                "normal",
                quality_current,
                quality_current,
                "review_quality_gate",
                "low quality annotations should tune upstream quality filters before lowering identity thresholds",
                evidence_counts,
                "medium" if low_quality_count >= 3 else "low",
            )
        )
    return {
        "sample_count": total,
        "attention_count": int(attention_count),
        "label_counts": _top_counts(label_counts, "label"),
        "recommendations": recommendations,
        "method": "review_annotation_heuristic",
        "auto_apply": False,
    }

def create_review_annotation(
    tenant_id: str,
    *,
    job_id: Any,
    track_id: Any,
    label: Any,
    reviewer: Any = None,
    note: Any = None,
    frame_index: int | None = None,
    evidence_ref: Any = None,
    source: str = "console",
    created_at: float | None = None,
) -> dict[str, Any]:
    record = {
        "annotation_id": f"rev_{secrets.token_hex(8)}",
        "tenant_id": bounded_text(tenant_id, "tenant_id", required=True, max_length=128),
        "job_id": bounded_text(job_id, "job_id", required=True),
        "track_id": bounded_text(track_id, "track_id", required=True),
        "label": normalize_review_label(label),
        "reviewer": bounded_text(reviewer, "reviewer", max_length=128) or "operator",
        "note": bounded_text(note, "note", max_length=MAX_REVIEW_NOTE_LENGTH),
        "frame_index": frame_index,
        "evidence_ref": bounded_text(evidence_ref, "evidence_ref", max_length=MAX_REVIEW_TEXT_LENGTH),
        "created_at": float(created_at if created_at is not None else wall_time()),
        "source": bounded_text(source, "source", max_length=64) or "console",
    }
    if frame_index is not None and frame_index < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="frame_index 必须大于等于 0")
    with _REVIEW_LOCK:
        _REVIEW_STATE["annotations"].append(record)
        if len(_REVIEW_STATE["annotations"]) > MAX_REVIEW_ANNOTATIONS:
            del _REVIEW_STATE["annotations"][: len(_REVIEW_STATE["annotations"]) - MAX_REVIEW_ANNOTATIONS]
        save_review_state()
        return public_review_annotation(record)


__all__ = [
    "MAX_REVIEW_DATASET_LIMIT",
    "MAX_REVIEW_LIST_LIMIT",
    "clear_review_state",
    "create_review_annotation",
    "list_review_annotations",
    "list_review_datasets",
    "load_review_state",
    "restore_review_state",
    "review_annotation_summary",
    "review_state_payload",
    "review_threshold_recommendations",
    "save_review_state",
]
