from copy import deepcopy
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.observability import logger, wall_time
from app.portrait_compare import l2_normalize_vector
from app.portrait_gallery_records import FeatureRecord, GalleryKey, PersonRecord, feature_object_infos, gallery_key
from app.portrait_response import exception_log_summary
from app.portrait_security import validate_person_id
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.portrait_thresholds import normalize_modality
from app.settings import PORTRAIT_GALLERY_STATE_PATH, PORTRAIT_STORAGE_BACKEND


def postgres_gallery_enabled() -> bool:
    return PORTRAIT_STORAGE_BACKEND == "postgres"


GALLERY: dict[GalleryKey, PersonRecord] = {}


def gallery_state_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "people": [person.state_dict() for person in sorted(GALLERY.values(), key=lambda item: (item.tenant_id, item.person_id))],
    }


def load_gallery_state() -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import load_gallery_snapshot

        payload = load_gallery_snapshot()
    else:
        payload = read_json_state(PORTRAIT_GALLERY_STATE_PATH, {"people": []})
    if not isinstance(payload, dict):
        handle_state_read_error(f"gallery state root must be a mapping: {PORTRAIT_GALLERY_STATE_PATH}")
        return
    people = payload.get("people", [])
    if not isinstance(people, list):
        handle_state_read_error(f"gallery state people must be a list: {PORTRAIT_GALLERY_STATE_PATH}")
        return
    GALLERY.clear()
    for item in people:
        if not isinstance(item, dict) or "person_id" not in item:
            continue
        try:
            person = PersonRecord.from_state(item)
            validate_person_id(person.person_id)
        except Exception as exc:
            logger.warning("skipping invalid gallery person state: %s", exception_log_summary(exc))
            continue
        GALLERY[gallery_key(person.tenant_id, person.person_id)] = person


def save_gallery_state() -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import replace_gallery_snapshot

        replace_gallery_snapshot(gallery_state_payload())
        return
    write_json_state(PORTRAIT_GALLERY_STATE_PATH, gallery_state_payload())


def persist_person(person: PersonRecord) -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import upsert_gallery_person

        upsert_gallery_person(person.state_dict())
        return
    save_gallery_state()


def persist_feature(person: PersonRecord, feature: FeatureRecord) -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import upsert_gallery_feature

        upsert_gallery_feature(person.tenant_id, person.person_id, feature.state_dict())
    else:
        save_gallery_state()

    try:
        from app.portrait_vector_store import VECTOR_STORE

        VECTOR_STORE.upsert_feature(person.public_dict(include_embeddings=False), feature.state_dict())
    except Exception as exc:
        logger.warning("vector upsert failed: %s", exception_log_summary(exc))


def persist_person_delete(tenant_id: str, person_id: str) -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import delete_gallery_person

        delete_gallery_person(tenant_id, person_id)
    else:
        save_gallery_state()

    try:
        from app.portrait_vector_store import VECTOR_STORE

        VECTOR_STORE.delete_person(tenant_id, person_id)
    except Exception as exc:
        logger.warning("vector delete failed: %s", exception_log_summary(exc))


def list_gallery_people(tenant_id: str = "default") -> list[dict[str, Any]]:
    return [
        person.public_dict()
        for person in sorted(GALLERY.values(), key=lambda item: item.person_id)
        if person.tenant_id == tenant_id
    ]


def upsert_person(
    person_id: str | None,
    display_name: str | None,
    metadata: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> PersonRecord:
    resolved_id = validate_person_id(person_id or f"p_{uuid4().hex[:12]}")
    key = gallery_key(tenant_id, resolved_id)
    person = GALLERY.get(key)
    previous_person = deepcopy(person) if person is not None else None
    if person is None:
        person = PersonRecord(tenant_id=tenant_id, person_id=resolved_id, display_name=display_name, metadata=metadata or {})
        GALLERY[key] = person
    else:
        if display_name is not None:
            person.display_name = display_name
        if metadata:
            person.metadata.update(metadata)
        person.updated_at = wall_time()
    try:
        persist_person(person)
    except Exception:
        if previous_person is None:
            GALLERY.pop(key, None)
        else:
            GALLERY[key] = previous_person
        raise
    return person


def add_feature(
    person: PersonRecord,
    *,
    modality: str,
    embedding: list[float],
    model_id: str,
    model_version: str,
    quality_score: float,
    source_id: str,
    object_info: dict[str, Any] | None = None,
) -> FeatureRecord:
    modality_key = normalize_modality(modality)
    feature = FeatureRecord(
        feature_id=f"f_{uuid4().hex[:16]}",
        modality=modality_key,
        embedding=embedding,
        embedding_dim=len(embedding),
        model_id=model_id,
        model_version=model_version,
        quality_score=round(float(quality_score), 6),
        source_id=source_id,
        created_at=wall_time(),
        object_info=deepcopy(object_info) if object_info else None,
    )
    previous_person = deepcopy(person)
    person.features.append(feature)
    person.updated_at = wall_time()
    try:
        persist_feature(person, feature)
    except Exception:
        key = gallery_key(person.tenant_id, person.person_id)
        GALLERY[key] = previous_person
        person.features = previous_person.features
        person.updated_at = previous_person.updated_at
        person.display_name = previous_person.display_name
        person.metadata = previous_person.metadata
        raise
    return feature


def get_person_or_404(person_id: str, tenant_id: str = "default") -> PersonRecord:
    resolved_id = validate_person_id(person_id)
    person = GALLERY.get(gallery_key(tenant_id, resolved_id))
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person not found")
    return person


def patch_person(person_id: str, payload: dict[str, Any], tenant_id: str = "default") -> PersonRecord:
    resolved_id = validate_person_id(person_id)
    person = get_person_or_404(resolved_id, tenant_id=tenant_id)
    previous_person = deepcopy(person)
    if "display_name" in payload:
        person.display_name = payload["display_name"]
    if isinstance(payload.get("metadata"), dict):
        person.metadata.update(payload["metadata"])
    person.updated_at = wall_time()
    try:
        persist_person(person)
    except Exception:
        GALLERY[gallery_key(tenant_id, resolved_id)] = previous_person
        person.display_name = previous_person.display_name
        person.metadata = previous_person.metadata
        person.updated_at = previous_person.updated_at
        person.features = previous_person.features
        raise
    return person


def delete_person(person_id: str, tenant_id: str = "default") -> bool:
    resolved_id = validate_person_id(person_id)
    key = gallery_key(tenant_id, resolved_id)
    removed = GALLERY.pop(key, None)
    if removed is not None:
        try:
            persist_person_delete(tenant_id, resolved_id)
        except Exception:
            GALLERY[key] = removed
            raise
        return True
    return False


def reindex_gallery_vectors(
    *,
    tenant_id: str = "default",
    modality: str | None = None,
    model_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    from app.portrait_vector_store import VECTOR_STORE

    modality_key = normalize_modality(modality) if modality else None
    model_key = str(model_id).strip() if model_id else None
    if model_key == "":
        model_key = None

    people = [
        person
        for person in sorted(GALLERY.values(), key=lambda item: item.person_id)
        if person.tenant_id == tenant_id
    ]
    vector_backend = str(getattr(VECTOR_STORE, "backend_name", "unknown"))
    feature_count = sum(len(person.features) for person in people)
    matched_feature_count = 0
    reindexed_feature_count = 0
    skipped_feature_count = 0
    failed_feature_count = 0
    skip_reasons: dict[str, int] = {}

    def skip(reason: str) -> None:
        nonlocal skipped_feature_count
        skipped_feature_count += 1
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for person in people:
        person_payload = person.public_dict(include_embeddings=False)
        for feature in person.features:
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
        "dry_run": bool(dry_run),
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
            str(risk)
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
        score = max(0.0, min(1.0, float(query_quality)))
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
            str(risk)
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
        feature = candidate.get("feature") if isinstance(candidate.get("feature"), dict) else {}
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
    feature = candidate.get("feature") if isinstance(candidate.get("feature"), dict) else {}
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
    feature = candidate.get("feature") if isinstance(candidate.get("feature"), dict) else {}
    try:
        return max(0.0, min(1.0, float(feature.get("quality_score", 0.0) or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def gallery_records_by_feature_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        feature = record.get("feature") if isinstance(record.get("feature"), dict) else {}
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
    if gate is not None and float(gate["score"]) < 0.40:
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
            str((candidate.get("feature") if isinstance(candidate.get("feature"), dict) else {}).get("feature_id", ""))
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
    records: list[dict[str, Any]] = []
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
    candidate_pool_size = min(500, max(top_k * 5, top_k + 10))
    initial_candidates = VECTOR_STORE.search(
        embedding,
        records,
        modality=modality_key,
        threshold_profile=threshold_profile,
        top_k=candidate_pool_size,
        tenant_id=tenant_id,
    )
    expansion_embedding, expansion_context = gallery_query_expansion_plan(
        embedding,
        initial_candidates,
        records,
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
            records,
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
