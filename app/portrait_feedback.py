from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any, TypeVar, cast
from uuid import uuid4

from fastapi import HTTPException, status

from app.portrait_control_state import ControlStateBackend, ControlStateLock
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import PORTRAIT_FEEDBACK_STATE_PATH

REVIEW_REASONS = {
    "low_confidence",
    "near_threshold",
    "model_disagreement",
    "rule_failure",
    "human_correction",
    "customer_feedback",
    "false_positive",
    "false_negative",
    "drift",
    "long_tail",
    "targeted_sample",
}
REVIEW_STATUSES = {"queued", "assigned", "exported", "reviewed", "accepted", "rejected", "deleted"}
ANNOTATION_FORMATS = {"label_studio", "cvat"}
DATASET_SPLITS = {"train", "validation", "test"}
_COLLECTIONS = (
    "review_samples",
    "annotation_exports",
    "annotation_imports",
    "dataset_manifests",
    "analysis_reports",
)
_LOCK = ControlStateLock()
_T = TypeVar("_T")


def _empty_state() -> dict[str, Any]:
    return {"revision": 0, **{name: [] for name in _COLLECTIONS}}


_STATE = _empty_state()


def now_seconds() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _copy(value: _T) -> _T:
    return copy.deepcopy(value)


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _scope(record: dict[str, Any], tenant_id: str, project_id: str) -> bool:
    return record.get("tenant_id") == tenant_id and record.get("project_id") == project_id


def _validate_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        handle_state_read_error("feedback state root must be an object")
        return _empty_state()
    state = _empty_state()
    try:
        state["revision"] = max(0, int(payload.get("revision", 0)))
    except (TypeError, ValueError):
        handle_state_read_error("feedback state revision is invalid")
    for name in _COLLECTIONS:
        value = payload.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            handle_state_read_error(f"feedback state collection is invalid: {name}")
            continue
        state[name] = _copy(value)
    return state


_BACKEND = ControlStateBackend("feedback", _STATE, _LOCK.raw, _empty_state, _validate_state)
_LOCK.bind(_BACKEND)


def load_feedback_state() -> None:
    with _LOCK:
        if _BACKEND.postgres_enabled():
            if _BACKEND.revision == 0:
                _save(increment=False)
            return
        _STATE.clear()
        _STATE.update(_validate_state(read_json_state(PORTRAIT_FEEDBACK_STATE_PATH, _empty_state())))


def reset_feedback_state(*, persist: bool = False) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_empty_state())
        if persist:
            _save()
        elif _BACKEND.postgres_enabled():
            _BACKEND.invalidate()


def feedback_state_payload() -> dict[str, Any]:
    with _LOCK:
        return _copy(_STATE)


def restore_feedback_state(payload: dict[str, Any]) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_validate_state(payload))
        _save(increment=False)


def _save(*, increment: bool = True) -> None:
    if increment:
        _STATE["revision"] = int(_STATE.get("revision", 0)) + 1
    if _BACKEND.postgres_enabled():
        _BACKEND.save(actor="feedback-control-plane")
    else:
        write_json_state(PORTRAIT_FEEDBACK_STATE_PATH, _STATE)


def public_review_sample(record: dict[str, Any]) -> dict[str, Any]:
    output = {key: _copy(value) for key, value in record.items() if key != "object_ref"}
    output["object_available"] = bool(record.get("object_ref"))
    return output


def create_review_sample(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        reason = str(payload.get("reason") or "").lower()
        if reason not in REVIEW_REASONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="review sample reason is invalid"
            )
        source_request_id = str(payload.get("source_request_id") or request_id).strip()
        source_item_id = str(payload.get("source_item_id") or "").strip()
        model_version_id = str(payload.get("model_version_id") or "").strip()
        if not source_item_id or not model_version_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="source item and model version are required"
            )
        deduplication_key = hashlib.sha256(
            f"{tenant_id}\0{project_id}\0{source_request_id}\0{source_item_id}\0{model_version_id}".encode()
        ).hexdigest()
        existing = next(
            (item for item in _STATE["review_samples"] if item.get("deduplication_key") == deduplication_key),
            None,
        )
        if existing is not None:
            return public_review_sample(existing)
        confidence = payload.get("confidence")
        confidence_value = float(confidence) if isinstance(confidence, (int, float)) else None
        priority = max(0, min(100, int(payload.get("priority") or 50)))
        risk_level = str(payload.get("risk_level") or "medium")[:32]
        selection_score = priority / 100.0
        if confidence_value is not None:
            selection_score += max(0.0, 0.5 - abs(confidence_value - 0.5))
        if risk_level in {"high", "critical"}:
            selection_score += 0.5
        timestamp = now_seconds()
        record = {
            "review_sample_id": new_id("review_sample"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "source_request_id": source_request_id[:128],
            "source_type": str(payload.get("source_type") or "inference")[:64],
            "source_item_id": source_item_id[:512],
            "reason": reason,
            "priority": priority,
            "risk_level": risk_level,
            "confidence": confidence_value,
            "selection_score": round(selection_score, 6),
            "model_id": str(payload.get("model_id") or "")[:256],
            "model_version_id": model_version_id[:256],
            "model_sha256": str(payload.get("model_sha256") or "")[:64],
            "contract_version": str(payload.get("contract_version") or "1.0")[:64],
            "object_ref": str(payload.get("object_ref") or "")[:2000],
            "masked_preview_ref": str(payload.get("masked_preview_ref") or "")[:2000],
            "content_sha256": str(payload.get("content_sha256") or "")[:64],
            "proposed_labels": _copy(payload.get("proposed_labels") or {}),
            "final_labels": None,
            "tags": sorted({str(item).strip()[:128] for item in payload.get("tags", []) if str(item).strip()}),
            "status": "queued",
            "assigned_to": None,
            "retention_policy_id": payload.get("retention_policy_id"),
            "expires_at": payload.get("expires_at"),
            "deduplication_key": deduplication_key,
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
            "updated_at": timestamp,
            "updated_by": actor[:256],
            "version": 1,
        }
        _STATE["review_samples"].append(record)
        _save()
        return public_review_sample(record)


def list_review_samples(
    tenant_id: str,
    project_id: str,
    *,
    status_filter: str | None = None,
    reason: str | None = None,
    risk_level: str | None = None,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [
            public_review_sample(item)
            for item in _STATE["review_samples"]
            if _scope(item, tenant_id, project_id)
            and (status_filter is None or item.get("status") == status_filter)
            and (reason is None or item.get("reason") == reason)
            and (risk_level is None or item.get("risk_level") == risk_level)
        ]
        ordered = sorted(
            rows,
            key=lambda item: (float(item.get("selection_score") or 0), float(item.get("created_at") or 0)),
            reverse=True,
        )
        return ordered if limit is None else ordered[:limit]


def _require_sample(tenant_id: str, project_id: str, sample_id: str) -> dict[str, Any]:
    sample = next(
        (
            item
            for item in _STATE["review_samples"]
            if _scope(item, tenant_id, project_id) and item.get("review_sample_id") == sample_id
        ),
        None,
    )
    if sample is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review sample does not exist")
    return cast(dict[str, Any], sample)


def export_review_samples(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        export_format = str(payload.get("format") or "label_studio").lower()
        if export_format not in ANNOTATION_FORMATS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="annotation export format is invalid"
            )
        sample_ids = list(dict.fromkeys(str(item) for item in payload.get("sample_ids", []) if str(item)))
        if not sample_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="review sample ids are required"
            )
        samples = [_require_sample(tenant_id, project_id, sample_id) for sample_id in sample_ids]
        tasks = []
        for sample in samples:
            task = {
                "id": sample["review_sample_id"],
                "data": {
                    "preview": sample.get("masked_preview_ref"),
                    "source_item_id": sample.get("source_item_id"),
                    "model_version_id": sample.get("model_version_id"),
                },
                "meta": {
                    "reason": sample.get("reason"),
                    "risk_level": sample.get("risk_level"),
                    "proposed_labels": _copy(sample.get("proposed_labels") or {}),
                },
            }
            tasks.append(task)
        canonical = json.dumps(tasks, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = now_seconds()
        record = {
            "annotation_export_id": new_id("annotation_export"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "format": export_format,
            "schema_version": str(payload.get("schema_version") or "1.0")[:64],
            "sample_ids": sample_ids,
            "sample_count": len(sample_ids),
            "tasks": tasks,
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "external_task_id": payload.get("external_task_id"),
            "status": "ready",
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
        }
        _STATE["annotation_exports"].append(record)
        for sample in samples:
            sample["status"] = "exported"
            sample["updated_at"] = timestamp
            sample["updated_by"] = actor[:256]
            sample["version"] = int(sample.get("version", 1)) + 1
        _save()
        return _copy(record)


def import_annotations(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        export_id = str(payload.get("annotation_export_id") or "")
        export = next(
            (
                item
                for item in _STATE["annotation_exports"]
                if _scope(item, tenant_id, project_id) and item.get("annotation_export_id") == export_id
            ),
            None,
        )
        if export is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="annotation export does not exist")
        annotations = payload.get("annotations") or []
        if not isinstance(annotations, list) or not annotations:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="annotations are required")
        seen: set[str] = set()
        normalized = []
        conflicts = []
        exported_ids = set(export.get("sample_ids") or [])
        for raw in annotations:
            if not isinstance(raw, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="annotation must be an object"
                )
            sample_id = str(raw.get("review_sample_id") or raw.get("id") or "")
            labels = raw.get("labels")
            if sample_id in seen:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="duplicate annotation sample id")
            if sample_id not in exported_ids or not isinstance(labels, dict) or not labels:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="annotation sample or labels are invalid"
                )
            sample = _require_sample(tenant_id, project_id, sample_id)
            if sample.get("final_labels") is not None and sample.get("final_labels") != labels:
                conflicts.append(
                    {
                        "review_sample_id": sample_id,
                        "existing": _copy(sample.get("final_labels")),
                        "incoming": _copy(labels),
                    }
                )
            seen.add(sample_id)
            normalized.append((sample, _copy(labels)))
        conflict_policy = str(payload.get("conflict_policy") or "reject").lower()
        if conflicts and conflict_policy == "reject":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "annotation conflicts detected", "conflict_count": len(conflicts)},
            )
        if conflict_policy not in {"reject", "overwrite", "keep_existing"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="annotation conflict policy is invalid"
            )
        timestamp = now_seconds()
        applied_count = 0
        for sample, labels in normalized:
            if sample.get("final_labels") is not None and conflict_policy == "keep_existing":
                continue
            sample["final_labels"] = labels
            sample["status"] = "reviewed"
            sample["updated_at"] = timestamp
            sample["updated_by"] = actor[:256]
            sample["version"] = int(sample.get("version", 1)) + 1
            applied_count += 1
        record = {
            "annotation_import_id": new_id("annotation_import"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "annotation_export_id": export_id,
            "schema_version": str(payload.get("schema_version") or export.get("schema_version") or "1.0")[:64],
            "annotation_count": len(normalized),
            "applied_count": applied_count,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "conflict_policy": conflict_policy,
            "status": "completed",
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
        }
        _STATE["annotation_imports"].append(record)
        export["status"] = "imported"
        export["imported_at"] = timestamp
        _save()
        return _copy(record)


def create_dataset_manifest(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        name = str(payload.get("name") or "").strip()
        version = str(payload.get("version") or "").strip()
        if not name or not version:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="dataset name and version are required"
            )
        if any(
            _scope(item, tenant_id, project_id) and item.get("name") == name and item.get("version") == version
            for item in _STATE["dataset_manifests"]
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="dataset version already exists")
        raw_splits = payload.get("splits") or {}
        if not isinstance(raw_splits, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="dataset splits must be an object"
            )
        splits: dict[str, list[str]] = {}
        seen: set[str] = set()
        leakage = []
        samples = []
        for split_name, raw_ids in raw_splits.items():
            if split_name not in DATASET_SPLITS or not isinstance(raw_ids, list):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="dataset split is invalid")
            ids = list(dict.fromkeys(str(item) for item in raw_ids if str(item)))
            for sample_id in ids:
                if sample_id in seen:
                    leakage.append(sample_id)
                sample = _require_sample(tenant_id, project_id, sample_id)
                if sample.get("status") not in {"reviewed", "accepted"} or not sample.get("final_labels"):
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="dataset sample is not reviewed")
                samples.append(
                    {
                        "review_sample_id": sample_id,
                        "split": split_name,
                        "content_sha256": sample.get("content_sha256"),
                        "labels": _copy(sample.get("final_labels")),
                        "proposed_labels": _copy(sample.get("proposed_labels") or {}),
                        "confidence": sample.get("confidence"),
                        "reason": sample.get("reason"),
                        "risk_level": sample.get("risk_level"),
                        "model_id": sample.get("model_id"),
                        "model_sha256": sample.get("model_sha256"),
                        "source_request_id": sample.get("source_request_id"),
                        "model_version_id": sample.get("model_version_id"),
                    }
                )
                seen.add(sample_id)
            splits[split_name] = ids
        if leakage:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "dataset split leakage detected", "sample_ids": sorted(set(leakage))},
            )
        if not samples:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="dataset requires reviewed samples"
            )
        manifest_body = {
            "name": name,
            "version": version,
            "definition_version": str(payload.get("definition_version") or "1.0"),
            "label_schema_version": str(payload.get("label_schema_version") or "1.0"),
            "splits": splits,
            "samples": sorted(samples, key=lambda item: (item["split"], item["review_sample_id"])),
        }
        timestamp = now_seconds()
        record = {
            "dataset_id": new_id("dataset"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            **manifest_body,
            "sample_count": len(samples),
            "sha256": _canonical_sha256(manifest_body),
            "immutable": True,
            "lineage": _copy(payload.get("lineage") or []),
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
        }
        _STATE["dataset_manifests"].append(record)
        _save()
        return _copy(record)


def list_dataset_manifests(
    tenant_id: str,
    project_id: str,
    *,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = []
        for item in _STATE["dataset_manifests"]:
            if not _scope(item, tenant_id, project_id):
                continue
            rows.append({key: _copy(value) for key, value in item.items() if key != "samples"})
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def get_dataset_manifest(tenant_id: str, project_id: str, dataset_id: str) -> dict[str, Any]:
    with _LOCK:
        record = next(
            (
                item
                for item in _STATE["dataset_manifests"]
                if _scope(item, tenant_id, project_id) and item.get("dataset_id") == dataset_id
            ),
            None,
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dataset manifest does not exist")
        return _copy(record)


def _binary_metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    true_positive = false_positive = true_negative = false_negative = 0
    for row in rows:
        predicted_positive = float(row["confidence"]) >= threshold
        actual_positive = bool(row["actual_positive"])
        if predicted_positive and actual_positive:
            true_positive += 1
        elif predicted_positive:
            false_positive += 1
        elif actual_positive:
            false_negative += 1
        else:
            true_negative += 1
    sample_count = len(rows)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else 0.0
    recall = true_positive / recall_denominator if recall_denominator else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (true_positive + true_negative) / sample_count if sample_count else 0.0
    return {
        "threshold": round(threshold, 6),
        "sample_count": sample_count,
        "confusion_matrix": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        },
        "accuracy": round(accuracy, 6),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "error_rate": round(1.0 - accuracy, 6),
    }


def _analysis_input_rows(
    tenant_id: str,
    project_id: str,
    manifests: list[dict[str, Any]],
    *,
    label_key: str,
    positive_value: Any,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    excluded = {"duplicate": 0, "missing_label": 0, "missing_confidence": 0, "invalid_confidence": 0}
    seen: set[str] = set()
    scoped_samples = {
        str(item.get("review_sample_id")): item
        for item in _STATE["review_samples"]
        if _scope(item, tenant_id, project_id)
    }
    for manifest in manifests:
        for snapshot in manifest.get("samples") or []:
            sample_id = str(snapshot.get("review_sample_id") or "")
            if sample_id in seen:
                excluded["duplicate"] += 1
                continue
            seen.add(sample_id)
            live_sample = scoped_samples.get(sample_id, {})
            labels = snapshot.get("labels")
            if not isinstance(labels, dict) or label_key not in labels:
                excluded["missing_label"] += 1
                continue
            confidence = snapshot.get("confidence", live_sample.get("confidence"))
            if confidence is None:
                excluded["missing_confidence"] += 1
                continue
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                excluded["invalid_confidence"] += 1
                continue
            confidence_value = float(confidence)
            if not 0.0 <= confidence_value <= 1.0:
                excluded["invalid_confidence"] += 1
                continue
            rows.append(
                {
                    "review_sample_id": sample_id,
                    "dataset_id": manifest.get("dataset_id"),
                    "split": snapshot.get("split"),
                    "content_sha256": snapshot.get("content_sha256"),
                    "model_id": snapshot.get("model_id", live_sample.get("model_id")),
                    "model_version_id": snapshot.get("model_version_id", live_sample.get("model_version_id")),
                    "model_sha256": snapshot.get("model_sha256", live_sample.get("model_sha256")),
                    "reason": snapshot.get("reason", live_sample.get("reason")),
                    "risk_level": snapshot.get("risk_level", live_sample.get("risk_level")),
                    "confidence": confidence_value,
                    "actual_positive": labels[label_key] == positive_value,
                }
            )
    return rows, excluded


def create_feedback_analysis_report(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        name = str(payload.get("name") or "").strip()
        version = str(payload.get("version") or "").strip()
        if not name or not version:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="analysis report name and version are required",
            )
        if any(
            _scope(item, tenant_id, project_id) and item.get("name") == name and item.get("version") == version
            for item in _STATE["analysis_reports"]
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="analysis report version already exists")

        dataset_ids = list(dict.fromkeys(str(item) for item in payload.get("dataset_ids") or [] if str(item)))
        manifests: list[dict[str, Any]] = []
        for dataset_id in dataset_ids:
            manifest = next(
                (
                    item
                    for item in _STATE["dataset_manifests"]
                    if _scope(item, tenant_id, project_id) and item.get("dataset_id") == dataset_id
                ),
                None,
            )
            if manifest is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dataset manifest does not exist")
            manifests.append(manifest)
        if not manifests:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="analysis report requires at least one dataset manifest",
            )

        label_key = str(payload.get("label_key") or "").strip()
        if not label_key:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="analysis label key is required")
        positive_value = _copy(payload.get("positive_value", True))
        current_threshold = float(payload.get("current_threshold", 0.5))
        minimum_sample_count = max(1, int(payload.get("minimum_sample_count", 20)))
        threshold_candidates = sorted(
            {float(item) for item in payload.get("threshold_candidates") or []} | {current_threshold}
        )
        if not threshold_candidates or any(not 0.0 <= item <= 1.0 for item in threshold_candidates):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="analysis threshold candidates must be between zero and one",
            )

        rows, excluded = _analysis_input_rows(
            tenant_id,
            project_id,
            manifests,
            label_key=label_key,
            positive_value=positive_value,
        )
        current_metrics = _binary_metrics(rows, current_threshold)
        by_reason: dict[str, int] = {}
        for row in rows:
            predicted_positive = float(row["confidence"]) >= current_threshold
            if predicted_positive == bool(row["actual_positive"]):
                continue
            reason = str(row.get("reason") or "unspecified")
            by_reason[reason] = by_reason.get(reason, 0) + 1
        enough_overall_data = len(rows) >= minimum_sample_count
        error_analysis = {
            "definition_version": "binary-error-analysis-v1",
            "status": "available" if enough_overall_data else "insufficient_data",
            "minimum_sample_count": minimum_sample_count,
            "scorable_sample_count": len(rows),
            "excluded_sample_count": sum(excluded.values()),
            "excluded_by_reason": excluded,
            "metrics": current_metrics,
            "errors_by_review_reason": dict(sorted(by_reason.items())),
        }

        threshold_curve = [_binary_metrics(rows, item) for item in threshold_candidates] if rows else []
        recommended = (
            max(
                threshold_curve,
                key=lambda item: (
                    float(item["f1"]),
                    float(item["accuracy"]),
                    -abs(float(item["threshold"]) - current_threshold),
                ),
            )
            if enough_overall_data
            else None
        )
        threshold_recommendation = {
            "definition_version": "threshold-recommendation-v1",
            "status": "available" if recommended is not None else "insufficient_data",
            "read_only": True,
            "configuration_changed": False,
            "current_threshold": round(current_threshold, 6),
            "recommended_threshold": recommended["threshold"] if recommended else None,
            "objective": "maximize_f1_then_accuracy",
            "minimum_sample_count": minimum_sample_count,
            "scorable_sample_count": len(rows),
            "candidate_metrics": threshold_curve,
            "reason": None
            if recommended is not None
            else f"requires at least {minimum_sample_count} scorable samples",
        }

        rows_by_version: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            model_version_id = str(row.get("model_version_id") or "unknown")
            rows_by_version.setdefault(model_version_id, []).append(row)
        model_metrics = []
        for model_version_id, version_rows in sorted(rows_by_version.items()):
            model_sha256 = sorted(
                {str(item.get("model_sha256")) for item in version_rows if item.get("model_sha256")}
            )
            model_metrics.append(
                {
                    "model_version_id": model_version_id,
                    "model_sha256": model_sha256,
                    "artifact_digest_consistent": len(model_sha256) <= 1,
                    "status": "inconsistent_model_artifact"
                    if len(model_sha256) > 1
                    else ("available" if len(version_rows) >= minimum_sample_count else "insufficient_data"),
                    "minimum_sample_count": minimum_sample_count,
                    **_binary_metrics(version_rows, current_threshold),
                }
            )
        baseline_version = str(payload.get("baseline_model_version_id") or "").strip()
        candidate_version = str(payload.get("candidate_model_version_id") or "").strip()
        if baseline_version == candidate_version:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="baseline and candidate model versions must differ",
            )
        metric_by_version = {str(item["model_version_id"]): item for item in model_metrics}
        baseline_metrics = metric_by_version.get(baseline_version)
        candidate_metrics = metric_by_version.get(candidate_version)
        comparison_available = bool(
            baseline_metrics
            and candidate_metrics
            and baseline_metrics["status"] == "available"
            and candidate_metrics["status"] == "available"
        )
        inconsistent_model_artifact = any(
            item is not None and item.get("status") == "inconsistent_model_artifact"
            for item in (baseline_metrics, candidate_metrics)
        )
        comparison_status = (
            "available"
            if comparison_available
            else ("inconsistent_model_artifact" if inconsistent_model_artifact else "insufficient_data")
        )
        deltas = (
            {
                metric: round(float(candidate_metrics[metric]) - float(baseline_metrics[metric]), 6)
                for metric in ("accuracy", "precision", "recall", "f1", "error_rate")
            }
            if comparison_available and baseline_metrics and candidate_metrics
            else None
        )
        model_comparison = {
            "definition_version": "model-comparison-v1",
            "status": comparison_status,
            "baseline_model_version_id": baseline_version,
            "candidate_model_version_id": candidate_version,
            "models": model_metrics,
            "deltas": deltas,
            "reason": None
            if comparison_available
            else (
                "a model version references multiple artifact digests"
                if inconsistent_model_artifact
                else "baseline and candidate each require the minimum scorable sample count"
            ),
        }

        release_criteria = {
            "minimum_accuracy": float(payload.get("minimum_accuracy", 0.8)),
            "minimum_f1": float(payload.get("minimum_f1", 0.8)),
            "maximum_accuracy_regression": float(payload.get("maximum_accuracy_regression", 0.02)),
            "maximum_f1_regression": float(payload.get("maximum_f1_regression", 0.02)),
        }
        blocking_reasons: list[str] = []
        if model_comparison["status"] == "inconsistent_model_artifact":
            blocking_reasons.append("model_artifact_inconsistent")
        elif not comparison_available or not candidate_metrics or deltas is None:
            blocking_reasons.append("insufficient_data")
        else:
            if float(candidate_metrics["accuracy"]) < release_criteria["minimum_accuracy"]:
                blocking_reasons.append("candidate_accuracy_below_minimum")
            if float(candidate_metrics["f1"]) < release_criteria["minimum_f1"]:
                blocking_reasons.append("candidate_f1_below_minimum")
            if float(deltas["accuracy"]) < -release_criteria["maximum_accuracy_regression"]:
                blocking_reasons.append("accuracy_regression_exceeds_limit")
            if float(deltas["f1"]) < -release_criteria["maximum_f1_regression"]:
                blocking_reasons.append("f1_regression_exceeds_limit")
        release_candidate = {
            "definition_version": "release-candidate-v1",
            "status": "insufficient_data"
            if "insufficient_data" in blocking_reasons
            else ("ready" if not blocking_reasons else "blocked"),
            "decision": "recommend_release" if not blocking_reasons else "hold",
            "candidate_model_version_id": candidate_version,
            "baseline_model_version_id": baseline_version,
            "criteria": release_criteria,
            "blocking_reasons": blocking_reasons,
            "human_approval_required": True,
        }

        evidence_manifests = [
            {
                "dataset_id": item.get("dataset_id"),
                "name": item.get("name"),
                "version": item.get("version"),
                "sha256": item.get("sha256"),
                "sample_count": item.get("sample_count"),
            }
            for item in manifests
        ]
        input_parameters = {
            "dataset_ids": dataset_ids,
            "label_key": label_key,
            "positive_value": positive_value,
            "current_threshold": current_threshold,
            "threshold_candidates": threshold_candidates,
            "minimum_sample_count": minimum_sample_count,
            "baseline_model_version_id": baseline_version,
            "candidate_model_version_id": candidate_version,
            **release_criteria,
        }
        evidence_summary = {
            "dataset_manifests": evidence_manifests,
            "manifest_evidence_sha256": _canonical_sha256(evidence_manifests),
            "sample_evidence_sha256": _canonical_sha256(rows),
            "input_sha256": _canonical_sha256({"parameters": input_parameters, "manifests": evidence_manifests}),
            "scorable_sample_count": len(rows),
            "excluded_sample_count": sum(excluded.values()),
        }
        report_body = {
            "name": name,
            "version": version,
            "definition_version": str(payload.get("definition_version") or "feedback-analysis-v1")[:64],
            "parameters": input_parameters,
            "error_analysis": error_analysis,
            "threshold_recommendation": threshold_recommendation,
            "model_comparison": model_comparison,
            "release_candidate": release_candidate,
            "evidence_summary": evidence_summary,
        }
        timestamp = now_seconds()
        report = {
            "analysis_report_id": new_id("feedback_analysis"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            **report_body,
            "status": "blocked"
            if model_comparison["status"] == "inconsistent_model_artifact"
            else (
                "insufficient_data"
                if error_analysis["status"] == "insufficient_data"
                or model_comparison["status"] == "insufficient_data"
                else "completed"
            ),
            "sha256": _canonical_sha256(report_body),
            "immutable": True,
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
        }
        _STATE["analysis_reports"].append(report)
        _save()
        return _copy(report)


def list_feedback_analysis_reports(
    tenant_id: str,
    project_id: str,
    *,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [
            {
                key: _copy(value)
                for key, value in item.items()
                if key not in {"error_analysis", "threshold_recommendation", "model_comparison"}
            }
            for item in _STATE["analysis_reports"]
            if _scope(item, tenant_id, project_id)
        ]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def get_feedback_analysis_report(
    tenant_id: str,
    project_id: str,
    analysis_report_id: str,
) -> dict[str, Any]:
    with _LOCK:
        report = next(
            (
                item
                for item in _STATE["analysis_reports"]
                if _scope(item, tenant_id, project_id) and item.get("analysis_report_id") == analysis_report_id
            ),
            None,
        )
        if report is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis report does not exist")
        return _copy(report)


load_feedback_state()


__all__ = [
    "create_dataset_manifest",
    "create_feedback_analysis_report",
    "create_review_sample",
    "export_review_samples",
    "feedback_state_payload",
    "get_dataset_manifest",
    "get_feedback_analysis_report",
    "import_annotations",
    "list_dataset_manifests",
    "list_feedback_analysis_reports",
    "list_review_samples",
    "load_feedback_state",
    "reset_feedback_state",
    "restore_feedback_state",
]
