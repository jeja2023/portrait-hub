from __future__ import annotations

from typing import Any

import numpy as np

from app.portrait_compare import cosine_similarity, l2_normalize_vector


def box_area(box: list[float]) -> float:
    if len(box) < 4:
        return 0.0
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def box_iou(box_a: list[float], box_b: list[float]) -> float:
    if len(box_a) < 4 or len(box_b) < 4:
        return 0.0
    x1 = max(float(box_a[0]), float(box_b[0]))
    y1 = max(float(box_a[1]), float(box_b[1]))
    x2 = min(float(box_a[2]), float(box_b[2]))
    y2 = min(float(box_a[3]), float(box_b[3]))
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = box_area(box_a) + box_area(box_b) - intersection
    return intersection / union if union > 0 else 0.0


def embedding_score(embedding_a: list[float] | None, embedding_b: list[float] | None) -> float | None:
    if not embedding_a or not embedding_b or len(embedding_a) != len(embedding_b):
        return None
    return max(0.0, min(1.0, (cosine_similarity(embedding_a, embedding_b) + 1.0) / 2.0))


def normalized_agreement(vector_a: np.ndarray, vector_b: np.ndarray) -> float:
    return max(0.0, min(1.0, (float(np.dot(vector_a, vector_b)) + 1.0) / 2.0))


def person_quality_score(person: dict[str, Any]) -> float:
    quality = person.get("quality")
    if isinstance(quality, dict):
        try:
            return max(0.0, min(1.0, float(quality.get("score", 0.0))))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def person_confidence(person: dict[str, Any]) -> float:
    try:
        return max(0.0, min(1.0, float(person.get("score", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def make_embedding_sample(frame_index: int, person: dict[str, Any], embedding: list[float] | None) -> dict[str, Any] | None:
    if not embedding:
        return None
    crop_quality = person.get("crop_quality") if isinstance(person.get("crop_quality"), dict) else {}
    return {
        "frame_index": frame_index,
        "embedding": [float(value) for value in embedding],
        "quality": person_quality_score(person),
        "crop_quality": float(crop_quality.get("score", person_quality_score(person))),
        "crop_usable": bool(crop_quality.get("usable", True)),
        "confidence": person_confidence(person),
    }


def temporal_recency_weight(frame_index: int, min_frame: int, max_frame: int) -> float:
    if max_frame <= min_frame:
        return 1.0
    position = (frame_index - min_frame) / float(max_frame - min_frame)
    return 0.85 + 0.15 * max(0.0, min(1.0, position))


def aggregate_track_template(
    samples: list[dict[str, Any]],
    *,
    include_embedding: bool = False,
) -> dict[str, Any]:
    valid = [
        sample
        for sample in samples
        if isinstance(sample.get("embedding"), list) and sample.get("embedding")
    ]
    if not valid:
        return {
            "embedding_dim": 0,
            "sample_count": 0,
            "aggregation": "quality_confidence_weighted_mean",
        }

    embedding_dim = len(valid[0]["embedding"])
    vectors: list[np.ndarray] = []
    weights: list[float] = []
    qualities: list[float] = []
    confidences: list[float] = []
    recencies: list[float] = []
    samples_used = 0
    frame_indexes = [int(sample.get("frame_index", 0)) for sample in valid]
    min_frame_index = min(frame_indexes) if frame_indexes else 0
    max_frame_index = max(frame_indexes) if frame_indexes else 0
    for sample in valid:
        if len(sample["embedding"]) != embedding_dim:
            continue
        quality = max(0.0, min(1.0, float(sample.get("quality", 0.0))))
        crop_quality = max(0.0, min(1.0, float(sample.get("crop_quality", quality))))
        if not bool(sample.get("crop_usable", True)):
            crop_quality *= 0.35
        confidence = max(0.0, min(1.0, float(sample.get("confidence", 0.0))))
        sample_quality = quality * 0.58 + crop_quality * 0.42
        recency = temporal_recency_weight(int(sample.get("frame_index", 0)), min_frame_index, max_frame_index)
        weight = (0.18 + 0.47 * sample_quality + 0.35 * confidence) * recency
        vectors.append(l2_normalize_vector(sample["embedding"]))
        weights.append(weight)
        qualities.append(sample_quality)
        confidences.append(confidence)
        recencies.append(recency)
        samples_used += 1

    if not vectors:
        return {
            "embedding_dim": 0,
            "sample_count": 0,
            "aggregation": "quality_confidence_weighted_mean",
        }

    if len(vectors) == 1:
        consensus_scores = [1.0]
        refined_weights = weights[:]
        consensus_floor = 1.0
        outlier_count = 0
    else:
        consensus_scores: list[float] = []
        for index, vector in enumerate(vectors):
            other_scores: list[float] = []
            for other_index, other in enumerate(vectors):
                if other_index == index:
                    continue
                other_scores.append(normalized_agreement(vector, other))
            consensus_scores.append(sum(other_scores) / max(1, len(other_scores)))
        median_consensus = float(np.median(consensus_scores))
        consensus_spread = float(np.std(consensus_scores))
        consensus_floor = (
            max(0.50, median_consensus - max(0.10, consensus_spread * 0.60))
            if len(vectors) >= 3
            else max(0.40, median_consensus - 0.12)
        )
        refined_weights = []
        outlier_count = 0
        for base_weight, consensus in zip(weights, consensus_scores):
            if len(vectors) >= 3 and consensus < consensus_floor:
                factor = 0.08
                outlier_count += 1
            else:
                factor = 0.16 + 0.84 * (consensus ** 2)
            refined_weights.append(base_weight * factor)

    if sum(refined_weights) <= 1e-9:
        refined_weights = weights[:]

    total_weight = max(1e-9, sum(refined_weights))
    template = sum(vector * weight for vector, weight in zip(vectors, refined_weights)) / total_weight
    template = l2_normalize_vector(template)
    consensus_score = sum(consensus * weight for consensus, weight in zip(consensus_scores, refined_weights)) / total_weight
    payload: dict[str, Any] = {
        "embedding_dim": int(template.shape[0]),
        "sample_count": len(vectors),
        "aggregation": "quality_confidence_weighted_mean",
        "quality": round(sum(quality * weight for quality, weight in zip(qualities, refined_weights)) / total_weight, 6),
        "confidence": round(sum(confidence * weight for confidence, weight in zip(confidences, refined_weights)) / total_weight, 6),
        "temporal": {
            "method": "linear_recency_decay",
            "frame_span": [min_frame_index, max_frame_index],
            "recency_p50": round(float(np.median(recencies)), 6) if recencies else 1.0,
            "recency_p95": round(float(np.percentile(recencies, 95)), 6) if recencies else 1.0,
        },
        "robustness": {
            "method": "pairwise_consensus_reweighting",
            "consensus_score": round(consensus_score, 6),
            "consensus_floor": round(consensus_floor, 6),
            "outlier_count": outlier_count,
            "sample_count": samples_used,
        },
    }
    if include_embedding:
        payload["embedding"] = [round(float(value), 8) for value in template.tolist()]
    return payload


def tracklet_quality_score(average_confidence: float, average_quality: float, stability: float, gap_count: int) -> float:
    gap_penalty = min(0.20, max(0, gap_count) * 0.03)
    score = average_quality * 0.45 + average_confidence * 0.35 + stability * 0.20 - gap_penalty
    return round(max(0.0, min(1.0, score)), 6)


def association_decision(details: dict[str, Any], min_score: float) -> dict[str, Any]:
    score = float(details.get("score", 0.0) or 0.0)
    geometry = float(details.get("iou", 0.0) or 0.0)
    motion = float(details.get("motion", 0.0) or 0.0)
    appearance_raw = details.get("appearance")
    appearance = None
    if appearance_raw is not None:
        try:
            appearance = float(appearance_raw)
        except (TypeError, ValueError):
            appearance = None
    margin = score - float(min_score)

    supporting_signals: list[str] = []
    if geometry >= 0.35:
        supporting_signals.append("geometry")
    elif geometry >= 0.08:
        supporting_signals.append("weak_geometry")
    if appearance is not None and appearance >= 0.86:
        supporting_signals.append("appearance")
    elif appearance is not None and appearance < 0.45:
        supporting_signals.append("appearance_conflict")
    if motion >= 0.65:
        supporting_signals.append("motion")

    if margin < 0.03:
        risk = "borderline"
    elif appearance is not None and appearance < 0.45 and geometry < 0.50:
        risk = "appearance_conflict"
    elif geometry < 0.05 and appearance is not None and appearance >= 0.86:
        risk = "appearance_rescue"
    elif geometry < 0.05 and appearance is None:
        risk = "motion_only"
    elif geometry < 0.15 and (appearance is None or appearance < 0.72):
        risk = "weak_support"
    else:
        risk = "clear"

    appearance_support = appearance if appearance is not None else motion
    confidence = (0.45 + margin / 0.50) * (
        0.55 + 0.30 * min(1.0, geometry / 0.50) + 0.15 * max(0.0, min(1.0, appearance_support))
    )
    risk_factors = [] if risk == "clear" else [risk]
    return {
        "margin": round(float(margin), 6),
        "confidence": round(max(0.0, min(1.0, confidence)), 6),
        "risk": risk,
        "risk_factors": risk_factors,
        "supporting_signals": supporting_signals or ["score_only"],
    }


def association_quality_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    if not decisions:
        return {
            "match_count": 0,
            "average_confidence": None,
            "min_margin": None,
            "risky_match_count": 0,
            "dominant_risk": "new_or_singleton",
            "risk_counts": {},
        }
    risk_counts: dict[str, int] = {}
    confidences: list[float] = []
    margins: list[float] = []
    for decision in decisions:
        risk = str(decision.get("risk", "clear"))
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        try:
            confidences.append(float(decision.get("confidence", 0.0)))
            margins.append(float(decision.get("margin", 0.0)))
        except (TypeError, ValueError):
            continue
    risky_match_count = sum(count for risk, count in risk_counts.items() if risk != "clear")
    dominant_risk = max(risk_counts, key=lambda risk: (risk_counts[risk], risk != "clear", risk))
    return {
        "match_count": len(decisions),
        "average_confidence": round(sum(confidences) / len(confidences), 6) if confidences else None,
        "min_margin": round(min(margins), 6) if margins else None,
        "risky_match_count": risky_match_count,
        "dominant_risk": dominant_risk,
        "risk_counts": risk_counts,
    }
