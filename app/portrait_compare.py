from typing import Any

import numpy as np

from app.portrait_thresholds import get_threshold, normalize_modality, validate_threshold_profile


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def coerce_quality_values(*qualities: float | None) -> list[float]:
    values: list[float] = []
    for value in qualities:
        if value is None:
            continue
        try:
            values.append(clamp_score(float(value)))
        except (TypeError, ValueError):
            continue
    return values


def l2_normalize_vector(vector: list[float] | np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if norm <= 0:
        return array
    return array / norm


def cosine_similarity(vector_a: list[float] | np.ndarray, vector_b: list[float] | np.ndarray) -> float:
    a = l2_normalize_vector(vector_a)
    b = l2_normalize_vector(vector_b)
    if a.shape != b.shape:
        raise ValueError(f"embedding dimensions do not match: {a.shape[0]} != {b.shape[0]}")
    return float(np.dot(a, b))


def euclidean_distance(vector_a: list[float] | np.ndarray, vector_b: list[float] | np.ndarray) -> float:
    a = l2_normalize_vector(vector_a)
    b = l2_normalize_vector(vector_b)
    if a.shape != b.shape:
        raise ValueError(f"embedding dimensions do not match: {a.shape[0]} != {b.shape[0]}")
    return float(np.linalg.norm(a - b))


def compare_embeddings(
    embedding_a: list[float] | np.ndarray,
    embedding_b: list[float] | np.ndarray,
    *,
    modality: str,
    threshold_profile: str = "normal",
) -> dict[str, Any]:
    similarity = cosine_similarity(embedding_a, embedding_b)
    distance = euclidean_distance(embedding_a, embedding_b)
    modality_key = normalize_modality(modality)
    profile_key = validate_threshold_profile(threshold_profile)
    threshold = get_threshold(modality_key, profile_key)
    return {
        "modality": modality_key,
        "similarity": round(similarity, 6),
        "distance": round(distance, 6),
        "threshold": threshold,
        "threshold_profile": profile_key,
        "passed": similarity >= threshold,
    }


def quality_aware_compare(
    embedding_a: list[float] | np.ndarray,
    embedding_b: list[float] | np.ndarray,
    *,
    modality: str,
    threshold_profile: str = "normal",
    quality_a: float | None = None,
    quality_b: float | None = None,
) -> dict[str, Any]:
    comparison = compare_embeddings(
        embedding_a,
        embedding_b,
        modality=modality,
        threshold_profile=threshold_profile,
    )
    quality_values = coerce_quality_values(quality_a, quality_b)
    quality = max(0.0, min(1.0, sum(quality_values) / len(quality_values))) if quality_values else 1.0
    min_quality = min(quality_values) if quality_values else 1.0
    quality_penalty = max(0.0, 0.55 - quality) * 0.18 + max(0.0, 0.25 - min_quality) * 0.12
    adjusted_threshold = min(0.99, float(comparison["threshold"]) + quality_penalty)
    adjusted_similarity = float(comparison["similarity"]) * (0.92 + 0.08 * quality)
    decision_margin = adjusted_similarity - adjusted_threshold
    if min_quality < 0.12:
        confidence = 0.0
        risk = "quality_unusable"
    else:
        confidence = max(0.0, min(1.0, (0.50 + decision_margin / 0.35) * (0.65 + 0.35 * quality)))
        if min_quality < 0.25 or quality < 0.35:
            risk = "low_quality"
        elif abs(decision_margin) < 0.03:
            risk = "borderline"
        else:
            risk = "clear"
    comparison.update(
        {
            "quality_adjusted_similarity": round(adjusted_similarity, 6),
            "quality_adjusted_threshold": round(adjusted_threshold, 6),
            "quality_penalty": round(quality_penalty, 6),
            "quality_gate": {
                "usable": min_quality >= 0.12,
                "score": round(quality, 6),
                "min_score": round(min_quality, 6),
            },
            "decision": {
                "margin": round(decision_margin, 6),
                "confidence": round(confidence, 6),
                "risk": risk,
            },
        }
    )
    comparison["passed"] = bool(min_quality >= 0.12 and adjusted_similarity >= adjusted_threshold)
    return comparison


def input_independence_decision(evidence: dict[str, Any]) -> dict[str, Any]:
    if evidence.get("exact_duplicate"):
        return {
            "independent": False,
            "risk": "duplicate_input",
            "confidence_multiplier": 0.35,
            "reason": "exact_or_perceptual_duplicate_input",
        }
    if evidence.get("near_duplicate"):
        return {
            "independent": False,
            "risk": "near_duplicate_input",
            "confidence_multiplier": 0.65,
            "reason": "near_duplicate_input",
        }
    return {
        "independent": True,
        "risk": "independent",
        "confidence_multiplier": 1.0,
        "reason": "independent_input_evidence",
    }


def apply_input_independence_to_decision(payload: dict[str, Any], evidence: dict[str, Any]) -> None:
    independence = input_independence_decision(evidence)
    decision = payload.setdefault("decision", {})
    existing_risk = str(decision.get("risk", "clear"))
    try:
        confidence = float(decision.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if not independence["independent"]:
        decision["confidence"] = round(max(0.0, min(1.0, confidence * independence["confidence_multiplier"])), 6)
        if existing_risk in {"clear", "borderline"}:
            decision["risk"] = independence["risk"]
    decision["input_independence"] = independence
    raw_risk_factors = decision.get("risk_factors", [])
    risk_factors = [
        str(risk)
        for risk in raw_risk_factors
        if isinstance(risk, str) and risk and risk != "clear"
    ] if isinstance(raw_risk_factors, list) else []
    if existing_risk != "clear" and existing_risk not in risk_factors:
        risk_factors.append(existing_risk)
    if not independence["independent"] and independence["risk"] not in risk_factors:
        risk_factors.append(independence["risk"])
    decision["risk_factors"] = risk_factors


def compare_track_templates(
    template_a: dict[str, Any],
    template_b: dict[str, Any],
    *,
    threshold_profile: str = "normal",
    modality: str = "body",
) -> dict[str, Any]:
    profile_key = validate_threshold_profile(threshold_profile)
    embedding_a = template_a.get("embedding") if isinstance(template_a, dict) else None
    embedding_b = template_b.get("embedding") if isinstance(template_b, dict) else None
    if not isinstance(embedding_a, list) or not isinstance(embedding_b, list):
        return {
            "modality": normalize_modality(modality),
            "threshold_profile": profile_key,
            "passed": False,
            "reason": "template_embedding_missing",
        }
    comparison = quality_aware_compare(
        embedding_a,
        embedding_b,
        modality=modality,
        threshold_profile=profile_key,
        quality_a=float(template_a.get("quality") if template_a.get("quality") is not None else 1.0),
        quality_b=float(template_b.get("quality") if template_b.get("quality") is not None else 1.0),
    )
    comparison["comparison_type"] = "track_template"
    comparison["template_a"] = {
        "sample_count": template_a.get("sample_count", 0),
        "quality": template_a.get("quality"),
        "confidence": template_a.get("confidence"),
    }
    comparison["template_b"] = {
        "sample_count": template_b.get("sample_count", 0),
        "quality": template_b.get("quality"),
        "confidence": template_b.get("confidence"),
    }
    return comparison


DEFAULT_FUSION_WEIGHTS = {
    "face": 0.42,
    "body": 0.34,
    "gait": 0.16,
    "appearance": 0.08,
}


def fuse_modalities(
    modalities: dict[str, dict[str, Any]],
    *,
    threshold_profile: str = "normal",
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    profile_key = validate_threshold_profile(threshold_profile)
    weights = weights or DEFAULT_FUSION_WEIGHTS
    weighted_sum = 0.0
    effective_weight = 0.0
    output_modalities: dict[str, dict[str, Any]] = {}
    used_scores: list[float] = []

    for raw_modality, item in modalities.items():
        modality = normalize_modality(raw_modality)
        score = item.get("score")
        quality = item.get("quality", 1.0)
        reason = item.get("reason")
        if score is None:
            output_modalities[modality] = {
                "score": None,
                "quality": quality if quality is not None else None,
                "used": False,
                "reason": reason or "score_missing",
            }
            continue
        try:
            quality_value = max(0.0, min(1.0, float(quality)))
            score_value = max(-1.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            output_modalities[modality] = {
                "score": None,
                "quality": None,
                "used": False,
                "reason": "invalid_score_or_quality",
            }
            continue

        if quality_value < 0.15:
            output_modalities[modality] = {
                "score": round(score_value, 6),
                "quality": round(quality_value, 6),
                "used": False,
                "reason": "quality_too_low",
            }
            continue

        weight = float(weights.get(modality, 0.0))
        contribution_weight = weight * quality_value
        weighted_sum += score_value * contribution_weight
        effective_weight += contribution_weight
        used_scores.append(score_value)
        output_modalities[modality] = {
            "score": round(score_value, 6),
            "quality": round(quality_value, 6),
            "used": contribution_weight > 0,
            "weight": weight,
        }

    raw_score = weighted_sum / effective_weight if effective_weight > 0 else 0.0
    if len(used_scores) >= 2:
        mean = sum(used_scores) / len(used_scores)
        variance = sum((score - mean) ** 2 for score in used_scores) / len(used_scores)
        score_std = float(np.sqrt(variance))
    else:
        score_std = 0.0
    agreement = max(0.0, min(1.0, 1.0 - score_std / 0.35))
    conflict_penalty = max(0.0, score_std - 0.18) * 0.35
    final_score = max(0.0, raw_score - conflict_penalty)
    threshold = get_threshold("fusion", profile_key)
    decision_margin = final_score - threshold
    evidence_factor = min(1.0, len(used_scores) / 2.0)
    confidence = max(0.0, min(1.0, (0.50 + decision_margin / 0.35) * (0.55 + 0.45 * agreement) * evidence_factor))
    if not used_scores:
        risk = "insufficient_evidence"
    elif agreement < 0.50:
        risk = "modality_conflict"
    elif abs(decision_margin) < 0.03:
        risk = "borderline"
    else:
        risk = "clear"
    return {
        "passed": final_score >= threshold,
        "final_score": round(final_score, 6),
        "raw_score": round(raw_score, 6),
        "threshold": threshold,
        "threshold_profile": profile_key,
        "decision": {
            "margin": round(decision_margin, 6),
            "confidence": round(confidence, 6),
            "risk": risk,
        },
        "consistency": {
            "used_count": len(used_scores),
            "score_std": round(score_std, 6),
            "agreement": round(agreement, 6),
            "conflict_penalty": round(conflict_penalty, 6),
        },
        "modalities": output_modalities,
    }
