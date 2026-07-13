from __future__ import annotations

from typing import Any

from app.gallery_state import GALLERY, GALLERY_LOCK
from app.observability import logger
from app.portrait_compare import l2_normalize_vector
from app.portrait_response import exception_log_summary
from app.portrait_thresholds import normalize_modality


# 查询质量分低于此值时，绝不会尝试保守的伪相关反馈扩展，因此调用方可以跳过本来
# 需要的图库快照物化（见 search_gallery / gallery_query_expansion_plan）。
QUERY_EXPANSION_MIN_QUERY_QUALITY = 0.40


def candidate_feature(candidate: dict[str, Any]) -> dict[str, Any]:
    feature = candidate.get("feature")
    return feature if isinstance(feature, dict) else {}


def gallery_records_snapshot(tenant_id: str, modality_key: str) -> list[dict[str, Any]]:
    # 物化某个 tenant/modality 的内存候选记录。GALLERY 与 person.features 会被并发
    # 执行 upsert 时会原地修改，因此迭代必须在锁内进行，避免 "changed size during iteration"。
    records: list[dict[str, Any]] = []
    with GALLERY_LOCK:
        for person in GALLERY.values():
            if person.tenant_id != tenant_id:
                continue
            for feature in person.features:
                if feature.modality != modality_key:
                    continue
                records.append(
                    {
                        "tenant_id": person.tenant_id,
                        "person_id": person.person_id,
                        "display_name": person.display_name,
                        "feature": feature.public_dict(include_embedding=False),
                        "embedding": feature.embedding,
                    }
                )
    return records


def query_expansion_quality_eligible(query_quality: float | None) -> bool:
    # 轻量预检，与 gallery_query_expansion_plan 内部的门槛一致：让热路径对永远不可能
    # 触发扩展的查询跳过构建图库快照。质量未知（None）时与此前一样仍视为合格。
    gate = gallery_query_quality_gate(query_quality)
    return not (gate is not None and float(gate["score"]) < QUERY_EXPANSION_MIN_QUERY_QUALITY)


def reindex_gallery_vectors(
    *,
    tenant_id: str = "default",
    modality: str | None = None,
    model_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    from app.portrait_vector_store import VECTOR_STORE

    modality_key = normalize_modality(modality) if modality else None
    model_key = model_id.strip() if model_id else None
    if model_key == "":
        model_key = None

    # 在锁保护下对匹配的人员及其特征列表进行快照，以便在下方较长的 I/O 绑定重构索引过程中，
    # 并发更新（这会就地修改 GALLERY 和 person.features）不会引发 "changed size during iteration"（迭代期间大小改变）的异常。
    with GALLERY_LOCK:
        people_with_features = [
            (person, list(person.features))
            for person in sorted(GALLERY.values(), key=lambda item: item.person_id)
            if person.tenant_id == tenant_id
        ]
    people = [person for person, _ in people_with_features]
    vector_backend = str(getattr(VECTOR_STORE, "backend_name", "unknown"))
    feature_count = sum(len(features) for _, features in people_with_features)
    matched_feature_count = 0
    reindexed_feature_count = 0
    skipped_feature_count = 0
    failed_feature_count = 0
    skip_reasons: dict[str, int] = {}

    def skip(reason: str) -> None:
        nonlocal skipped_feature_count
        skipped_feature_count += 1
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for person, person_features in people_with_features:
        person_payload = person.public_dict(include_embeddings=False)
        for feature in person_features:
            if modality_key and feature.modality != modality_key:
                skip("filtered_out")
                continue
            if model_key and feature.model_id != model_key:
                skip("filtered_out")
                continue

            matched_feature_count += 1
            if not feature.embedding:
                skip("embedding_missing")
                continue
            if dry_run:
                continue

            try:
                VECTOR_STORE.upsert_feature(person_payload, feature.state_dict())
                reindexed_feature_count += 1
            except Exception as exc:
                failed_feature_count += 1
                logger.warning("gallery vector reindex failed: %s", exception_log_summary(exc))

    if dry_run:
        status_value = "dry_run"
    elif failed_feature_count:
        status_value = "partial_failure" if reindexed_feature_count else "failed"
    else:
        status_value = "rebuilt"

    return {
        "status": status_value,
        "vector_backend": vector_backend,
        "person_count": len(people),
        "feature_count": feature_count,
        "matched_feature_count": matched_feature_count,
        "reindexed_feature_count": reindexed_feature_count,
        "skipped_feature_count": skipped_feature_count,
        "failed_feature_count": failed_feature_count,
        "error_count": failed_feature_count,
        "dry_run": dry_run,
        "filters": {
            "modality": modality_key,
            "model_id": model_key,
        },
        "skip_reasons": skip_reasons,
    }


def gallery_candidate_rank_context(candidates: list[dict[str, Any]], index: int) -> dict[str, Any]:
    current = candidates[index]
    current_score = float(current.get("template_similarity", 0.0) or 0.0)
    previous_gap = None
    next_gap = None
    competitors: list[tuple[float, dict[str, Any]]] = []
    if index > 0:
        previous = candidates[index - 1]
        previous_gap = max(0.0, float(previous.get("template_similarity", 0.0) or 0.0) - current_score)
        competitors.append((previous_gap, previous))
    if index + 1 < len(candidates):
        next_item = candidates[index + 1]
        next_gap = max(0.0, current_score - float(next_item.get("template_similarity", 0.0) or 0.0))
        competitors.append((next_gap, next_item))

    closest_gap = None
    closest = None
    if competitors:
        closest_gap, closest = min(competitors, key=lambda item: item[0])
    if closest_gap is not None and closest_gap < 0.025:
        ambiguity_risk = "rank_ambiguous"
    elif closest_gap is not None and closest_gap < 0.05:
        ambiguity_risk = "rank_close"
    else:
        ambiguity_risk = "clear"

    return {
        "rank": index + 1,
        "score_gap_to_previous": round(previous_gap, 6) if previous_gap is not None else None,
        "score_gap_to_next": round(next_gap, 6) if next_gap is not None else None,
        "closest_competitor_person_id": closest.get("person_id") if closest else None,
        "closest_competitor_gap": round(closest_gap, 6) if closest_gap is not None else None,
        "ambiguity_risk": ambiguity_risk,
    }


def apply_gallery_rank_context(candidates: list[dict[str, Any]]) -> None:
    for index, candidate in enumerate(candidates):
        rank_context = gallery_candidate_rank_context(candidates, index)
        candidate["rank_context"] = rank_context
        decision = candidate.setdefault("decision", {})
        existing_risk = str(decision.get("risk", "clear"))
        raw_risk_factors = decision.get("risk_factors", [])
        risk_factors = [
            risk
            for risk in raw_risk_factors
            if isinstance(risk, str) and risk and risk != "clear"
        ] if isinstance(raw_risk_factors, list) else []
        if existing_risk != "clear" and existing_risk not in risk_factors:
            risk_factors.append(existing_risk)

        ambiguity_risk = str(rank_context["ambiguity_risk"])
        if ambiguity_risk != "clear":
            try:
                confidence = float(decision.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            multiplier = 0.70 if ambiguity_risk == "rank_ambiguous" else 0.88
            decision["confidence"] = round(max(0.0, min(1.0, confidence * multiplier)), 6)
            decision["risk"] = gallery_primary_risk(existing_risk, ambiguity_risk)
            if ambiguity_risk not in risk_factors:
                risk_factors.append(ambiguity_risk)
        decision["risk_factors"] = risk_factors


def gallery_decision_risk_severity(risk: str) -> int:
    return {
        "clear": 0,
        "weak_query_quality": 1,
        "rank_close": 2,
        "borderline": 3,
        "low_query_quality": 4,
        "rank_ambiguous": 5,
        "single_feature_borderline": 6,
        "low_template_quality": 7,
        "query_quality_unusable": 8,
    }.get(risk, 3)


def gallery_primary_risk(existing_risk: str, candidate_risk: str) -> str:
    if gallery_decision_risk_severity(candidate_risk) > gallery_decision_risk_severity(existing_risk):
        return candidate_risk
    return existing_risk


def gallery_query_quality_gate(query_quality: float | None) -> dict[str, Any] | None:
    if query_quality is None:
        return None
    try:
        score = max(0.0, min(1.0, query_quality))
    except (TypeError, ValueError):
        return None

    if score < 0.12:
        risk = "query_quality_unusable"
        confidence_multiplier = 0.25
    elif score < 0.25:
        risk = "low_query_quality"
        confidence_multiplier = 0.55
    elif score < 0.40:
        risk = "weak_query_quality"
        confidence_multiplier = 0.78
    else:
        risk = "clear"
        confidence_multiplier = 1.0

    return {
        "score": round(score, 6),
        "usable": score >= 0.12,
        "risk": risk,
        "confidence_multiplier": confidence_multiplier,
    }


def apply_gallery_query_quality(candidates: list[dict[str, Any]], query_quality: float | None) -> None:
    gate = gallery_query_quality_gate(query_quality)
    if gate is None:
        return

    query_risk = str(gate["risk"])
    for candidate in candidates:
        decision = candidate.setdefault("decision", {})
        decision["query_quality"] = dict(gate)
        if query_risk == "clear":
            continue

        raw_risk_factors = decision.get("risk_factors", [])
        risk_factors = [
            risk
            for risk in raw_risk_factors
            if isinstance(risk, str) and risk and risk != "clear"
        ] if isinstance(raw_risk_factors, list) else []

        existing_risk = str(decision.get("risk", "clear"))
        if existing_risk != "clear" and existing_risk not in risk_factors:
            risk_factors.append(existing_risk)
        if query_risk not in risk_factors:
            risk_factors.append(query_risk)

        try:
            confidence = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        decision["confidence"] = round(
            max(0.0, min(1.0, confidence * float(gate["confidence_multiplier"]))),
            6,
        )
        decision["risk"] = gallery_primary_risk(existing_risk, query_risk)
        decision["risk_factors"] = risk_factors


def aggregate_gallery_candidates(
    candidates: list[dict[str, Any]],
    top_k: int,
    query_quality: float | None = None,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = (str(candidate.get("tenant_id", "default")), str(candidate.get("person_id", "")))
        feature = candidate_feature(candidate)
        quality = max(0.0, min(1.0, float(feature.get("quality_score", 0.0) or 0.0)))
        similarity = float(candidate.get("similarity", 0.0) or 0.0)
        adjusted = similarity * (0.90 + 0.10 * quality)
        group = groups.setdefault(
            key,
            {
                "tenant_id": candidate.get("tenant_id"),
                "person_id": candidate.get("person_id"),
                "display_name": candidate.get("display_name"),
                "feature_candidates": [],
                "weighted_similarity_sum": 0.0,
                "quality_weighted_sum": 0.0,
                "weight_sum": 0.0,
                "best_adjusted_similarity": -1.0,
                "best_candidate": candidate,
            },
        )
        weight = 0.35 + 0.65 * quality
        group["feature_candidates"].append(candidate)
        group["weighted_similarity_sum"] += similarity * weight
        group["quality_weighted_sum"] += quality * weight
        group["weight_sum"] += weight
        if adjusted > group["best_adjusted_similarity"]:
            group["best_adjusted_similarity"] = adjusted
            group["best_candidate"] = candidate

    aggregated = []
    for group in groups.values():
        best = dict(group["best_candidate"])
        weighted_mean = group["weighted_similarity_sum"] / max(group["weight_sum"], 1e-9)
        template_quality = group["quality_weighted_sum"] / max(group["weight_sum"], 1e-9)
        template_similarity = 0.65 * group["best_adjusted_similarity"] + 0.35 * weighted_mean
        threshold = float(best.get("threshold", 0.0) or 0.0)
        margin = template_similarity - threshold
        support = len(group["feature_candidates"])
        support_factor = min(1.0, support / 3.0)
        confidence = max(
            0.0,
            min(
                1.0,
                (0.50 + margin / 0.35) * (0.70 + 0.30 * template_quality) * (0.75 + 0.25 * support_factor),
            ),
        )
        if template_quality < 0.25:
            risk = "low_template_quality"
        elif support <= 1 and abs(margin) < 0.06:
            risk = "single_feature_borderline"
        elif abs(margin) < 0.03:
            risk = "borderline"
        else:
            risk = "clear"
        best["template_similarity"] = round(template_similarity, 6)
        best["quality_adjusted_similarity"] = round(float(group["best_adjusted_similarity"]), 6)
        best["template_quality"] = round(template_quality, 6)
        best["supporting_feature_count"] = len(group["feature_candidates"])
        best["decision"] = {
            "margin": round(margin, 6),
            "confidence": round(confidence, 6),
            "risk": risk,
            "risk_factors": [] if risk == "clear" else [risk],
            "support_factor": round(support_factor, 6),
        }
        best["feature_candidates"] = [
            {key: value for key, value in item.items() if key != "embedding"}
            for item in sorted(group["feature_candidates"], key=lambda item: item.get("similarity", 0.0), reverse=True)[:5]
        ]
        best["passed"] = template_similarity >= threshold
        aggregated.append(best)
    aggregated.sort(key=lambda item: item["template_similarity"], reverse=True)
    apply_gallery_query_quality(aggregated, query_quality)
    apply_gallery_rank_context(aggregated)
    return aggregated[:top_k]


def gallery_candidate_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    feature = candidate_feature(candidate)
    return (
        str(candidate.get("tenant_id", "default")),
        str(candidate.get("person_id", "")),
        str(feature.get("feature_id", "")),
    )


def gallery_candidate_score(candidate: dict[str, Any]) -> float:
    try:
        return max(0.0, min(1.0, float(candidate.get("similarity", 0.0) or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def gallery_candidate_quality(candidate: dict[str, Any]) -> float:
    feature = candidate_feature(candidate)
    try:
        return max(0.0, min(1.0, float(feature.get("quality_score", 0.0) or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def gallery_records_by_feature_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        feature = candidate_feature(record)
        feature_id = str(feature.get("feature_id", ""))
        if feature_id:
            index[feature_id] = record
    return index


def gallery_query_expansion_plan(
    embedding: list[float],
    candidates: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    query_quality: float | None = None,
    max_features: int = 3,
) -> tuple[list[float] | None, dict[str, Any]]:
    gate = gallery_query_quality_gate(query_quality)
    if gate is not None and float(gate["score"]) < QUERY_EXPANSION_MIN_QUERY_QUALITY:
        return None, {
            "enabled": False,
            "reason": "query_quality_too_low",
            "query_quality": gate,
            "selected_feature_count": 0,
            "selected_person_count": 0,
        }

    record_index = gallery_records_by_feature_id(records)
    selected: list[tuple[float, list[float], dict[str, Any]]] = []
    selected_people: set[str] = set()
    for candidate in sorted(candidates, key=gallery_candidate_score, reverse=True):
        key = gallery_candidate_key(candidate)
        feature_id = key[2]
        person_id = key[1]
        if not feature_id or person_id in selected_people:
            continue
        record = record_index.get(feature_id)
        if not record or not isinstance(record.get("embedding"), list):
            continue
        candidate_embedding = record["embedding"]
        if len(candidate_embedding) != len(embedding):
            continue
        score = gallery_candidate_score(candidate)
        threshold = float(candidate.get("threshold", 0.0) or 0.0)
        quality = gallery_candidate_quality(candidate)
        margin = score - threshold
        if quality < 0.45 or margin < 0.04:
            continue
        weight = max(0.01, margin) * (0.65 + 0.35 * quality)
        selected.append((weight, [float(value) for value in candidate_embedding], candidate))
        selected_people.add(person_id)
        if len(selected) >= max_features:
            break

    if not selected:
        return None, {
            "enabled": False,
            "reason": "no_stable_seed_candidates",
            "query_quality": gate,
            "selected_feature_count": 0,
            "selected_person_count": 0,
        }

    total_weight = sum(weight for weight, _, _ in selected)
    centroid = None
    for weight, candidate_embedding, _ in selected:
        vector = l2_normalize_vector(candidate_embedding)
        centroid = vector * (weight / total_weight) if centroid is None else centroid + vector * (weight / total_weight)
    if centroid is None:
        return None, {
            "enabled": False,
            "reason": "centroid_unavailable",
            "query_quality": gate,
            "selected_feature_count": 0,
            "selected_person_count": 0,
        }

    original = l2_normalize_vector(embedding)
    query_weight = 0.58
    centroid_weight = 0.42
    expanded = l2_normalize_vector(original * query_weight + l2_normalize_vector(centroid) * centroid_weight)
    return [round(float(value), 8) for value in expanded.tolist()], {
        "enabled": True,
        "method": "conservative_pseudo_relevance_feedback",
        "query_weight": query_weight,
        "centroid_weight": centroid_weight,
        "max_seed_features": max_features,
        "selected_feature_count": len(selected),
        "selected_person_count": len(selected_people),
        "seed_feature_ids": [
            str(candidate_feature(candidate).get("feature_id", ""))
            for _, _, candidate in selected
        ],
        "seed_person_ids": [str(candidate.get("person_id", "")) for _, _, candidate in selected],
        "query_quality": gate,
    }


def merge_gallery_candidate_pools(
    initial_candidates: list[dict[str, Any]],
    expanded_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for candidate in initial_candidates:
        payload = dict(candidate)
        payload["retrieval_stage"] = "initial"
        payload["initial_similarity"] = payload.get("similarity")
        merged[gallery_candidate_key(candidate)] = payload

    for candidate in expanded_candidates:
        key = gallery_candidate_key(candidate)
        expanded_score = gallery_candidate_score(candidate)
        existing = merged.get(key)
        if existing is None:
            payload = dict(candidate)
            payload["retrieval_stage"] = "expanded"
            payload["expanded_similarity"] = payload.get("similarity")
            merged[key] = payload
            continue
        initial_score = gallery_candidate_score(existing)
        blended = expanded_score * 0.72 + initial_score * 0.28
        existing.update(candidate)
        existing["retrieval_stage"] = "initial_and_expanded"
        existing["initial_similarity"] = round(initial_score, 6)
        existing["expanded_similarity"] = round(expanded_score, 6)
        existing["similarity"] = round(blended, 6)
        existing["distance"] = round(max(0.0, 1.0 - blended), 6)
        existing["passed"] = blended >= float(existing.get("threshold", 0.0) or 0.0)
    return sorted(merged.values(), key=gallery_candidate_score, reverse=True)


def search_gallery(
    embedding: list[float],
    *,
    modality: str,
    threshold_profile: str,
    top_k: int,
    tenant_id: str = "default",
    query_quality: float | None = None,
) -> list[dict[str, Any]]:
    from app.portrait_vector_store import VECTOR_STORE

    modality_key = normalize_modality(modality)
    backend = getattr(VECTOR_STORE, "backend_name", "local_numpy")
    is_local_scan = backend == "local_numpy"

    # 本地 numpy 后端会扫描此快照；pgvector/qdrant 查询各自的 ANN 索引并忽略它。
    # 至多构建一次、且仅在确有代码路径需要时构建，使 DB 后端图库在热路径上避免
    # 避免 O(N) 的持锁拷贝。查询扩展仍需要种子候选向量，因此当查询质量满足
    # 扩展条件时才物化快照。
    records_cache: list[dict[str, Any]] | None = None

    def gallery_records() -> list[dict[str, Any]]:
        nonlocal records_cache
        if records_cache is None:
            records_cache = gallery_records_snapshot(tenant_id, modality_key)
        return records_cache

    scan_records = gallery_records() if is_local_scan else []
    expansion_records = (
        gallery_records() if is_local_scan or query_expansion_quality_eligible(query_quality) else []
    )

    candidate_pool_size = min(500, max(top_k * 5, top_k + 10))
    initial_candidates = VECTOR_STORE.search(
        embedding,
        scan_records,
        modality=modality_key,
        threshold_profile=threshold_profile,
        top_k=candidate_pool_size,
        tenant_id=tenant_id,
    )
    expansion_embedding, expansion_context = gallery_query_expansion_plan(
        embedding,
        initial_candidates,
        expansion_records,
        query_quality=query_quality,
    )
    retrieval_context: dict[str, Any] = {
        "strategy": "single_stage",
        "candidate_pool_size": candidate_pool_size,
        "initial_candidate_count": len(initial_candidates),
        "query_expansion": expansion_context,
    }
    candidates = initial_candidates
    if expansion_embedding is not None:
        expansion_pool_size = min(500, max(candidate_pool_size, top_k * 8, top_k + 20))
        expanded_candidates = VECTOR_STORE.search(
            expansion_embedding,
            scan_records,
            modality=modality_key,
            threshold_profile=threshold_profile,
            top_k=expansion_pool_size,
            tenant_id=tenant_id,
        )
        candidates = merge_gallery_candidate_pools(initial_candidates, expanded_candidates)
        retrieval_context.update(
            {
                "strategy": "two_stage_query_expansion",
                "expansion_candidate_pool_size": expansion_pool_size,
                "expanded_candidate_count": len(expanded_candidates),
                "merged_candidate_count": len(candidates),
            }
        )

    aggregated = aggregate_gallery_candidates(candidates, top_k, query_quality=query_quality)
    for candidate in aggregated:
        candidate["retrieval_context"] = retrieval_context
    return aggregated


__all__ = [
    "GALLERY",
    "aggregate_gallery_candidates",
    "apply_gallery_query_quality",
    "apply_gallery_rank_context",
    "candidate_feature",
    "gallery_candidate_key",
    "gallery_candidate_quality",
    "gallery_candidate_rank_context",
    "gallery_candidate_score",
    "gallery_decision_risk_severity",
    "gallery_primary_risk",
    "gallery_query_expansion_plan",
    "gallery_query_quality_gate",
    "gallery_records_by_feature_id",
    "gallery_records_snapshot",
    "merge_gallery_candidate_pools",
    "query_expansion_quality_eligible",
    "reindex_gallery_vectors",
    "search_gallery",
]
