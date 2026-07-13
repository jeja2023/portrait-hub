"""针对 PortraitHub 输出结果的离线算法指标评估脚本。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import yaml

from app.portrait_tracking import box_iou


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file) if path.suffix.lower() == ".json" else yaml.safe_load(file)
    if not isinstance(payload, dict):
        raise ValueError("manifest 根节点必须是映射")
    return payload


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def roc_auc(scores: list[float], labels: list[int]) -> float | None:
    positives = [score for score, label in zip(scores, labels) if label == 1]
    negatives = [score for score, label in zip(scores, labels) if label == 0]
    if not positives or not negatives:
        return None
    wins = 0.0
    for pos in positives:
        for neg in negatives:
            if pos > neg:
                wins += 1.0
            elif pos == neg:
                wins += 0.5
    return round(wins / (len(positives) * len(negatives)), 6)


def tar_at_far(scores: list[float], labels: list[int], far: float) -> dict[str, Any]:
    positives = [score for score, label in zip(scores, labels) if label == 1]
    negatives = sorted([score for score, label in zip(scores, labels) if label == 0], reverse=True)
    if not positives or not negatives:
        return {"far": far, "threshold": None, "tar": None}
    allowed_false_accepts = int(far * len(negatives))
    if allowed_false_accepts <= 0:
        threshold = negatives[0] + 1e-9
    else:
        threshold = negatives[min(allowed_false_accepts - 1, len(negatives) - 1)]
    true_accepts = sum(1 for score in positives if score >= threshold)
    return {"far": far, "threshold": round(threshold, 6), "tar": round(true_accepts / len(positives), 6)}


def threshold_report(scores: list[float], labels: list[int], threshold: float) -> dict[str, Any]:
    tp = sum(1 for score, label in zip(scores, labels) if score >= threshold and label == 1)
    fp = sum(1 for score, label in zip(scores, labels) if score >= threshold and label == 0)
    tn = sum(1 for score, label in zip(scores, labels) if score < threshold and label == 0)
    fn = sum(1 for score, label in zip(scores, labels) if score < threshold and label == 1)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    fnr = fn / (fn + tp) if fn + tp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / max(1, len(labels))
    return {
        "threshold": round(threshold, 6),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "fpr": round(fpr, 6),
        "fnr": round(fnr, 6),
        "f1": round(f1, 6),
        "accuracy": round(accuracy, 6),
        "youden_j": round(recall - fpr, 6),
    }


def review_band_report(scores: list[float], labels: list[int], threshold: float, band_width: float = 0.03) -> dict[str, Any]:
    lower = threshold - band_width
    upper = threshold + band_width
    in_band = [(score, label) for score, label in zip(scores, labels) if lower <= score <= upper]
    positives = sum(1 for _, label in in_band if label == 1)
    negatives = len(in_band) - positives
    return {
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "count": len(in_band),
        "positive_count": positives,
        "negative_count": negatives,
        "positive_rate": round(positives / len(in_band), 6) if in_band else None,
    }


def score_separation(scores: list[float], labels: list[int]) -> dict[str, Any]:
    positives = [score for score, label in zip(scores, labels) if label == 1]
    negatives = [score for score, label in zip(scores, labels) if label == 0]
    if not positives or not negatives:
        return {"positive_mean": None, "negative_mean": None, "mean_gap": None, "d_prime": None}
    positive_mean = sum(positives) / len(positives)
    negative_mean = sum(negatives) / len(negatives)
    positive_var = sum((score - positive_mean) ** 2 for score in positives) / len(positives)
    negative_var = sum((score - negative_mean) ** 2 for score in negatives) / len(negatives)
    pooled_std = ((positive_var + negative_var) / 2.0) ** 0.5
    d_prime = (positive_mean - negative_mean) / pooled_std if pooled_std > 1e-12 else None
    return {
        "positive_mean": round(positive_mean, 6),
        "negative_mean": round(negative_mean, 6),
        "mean_gap": round(positive_mean - negative_mean, 6),
        "positive_p05": percentile(positives, 0.05),
        "positive_p50": percentile(positives, 0.50),
        "negative_p95": percentile(negatives, 0.95),
        "negative_p50": percentile(negatives, 0.50),
        "d_prime": round(d_prime, 6) if d_prime is not None else None,
    }


def calibrate_thresholds(scores: list[float], labels: list[int]) -> dict[str, Any]:
    if not scores or len(set(labels)) < 2:
        return {"best_f1": None, "best_youden": None, "eer": None, "recommended": None, "review_band": None}
    candidates = sorted(set(scores), reverse=True)
    reports = [threshold_report(scores, labels, threshold) for threshold in candidates]
    best_f1 = max(reports, key=lambda item: (item["f1"], item["accuracy"], item["threshold"]))
    best_youden = max(reports, key=lambda item: (item["youden_j"], item["accuracy"], item["threshold"]))
    eer_report = min(reports, key=lambda item: (abs(item["fpr"] - item["fnr"]), item["threshold"]))
    eer = (eer_report["fpr"] + eer_report["fnr"]) / 2.0
    recommended = max(reports, key=lambda item: (item["youden_j"], item["precision"], item["threshold"]))
    return {
        "best_f1": best_f1,
        "best_youden": best_youden,
        "eer": {
            "threshold": eer_report["threshold"],
            "eer": round(eer, 6),
            "fpr": eer_report["fpr"],
            "fnr": eer_report["fnr"],
        },
        "recommended": {
            **recommended,
            "policy": "maximize_youden_then_precision",
        },
        "review_band": review_band_report(scores, labels, float(recommended["threshold"])),
    }


def evaluate_pairs(pairs: list[dict[str, Any]], fars: list[float] | None = None) -> dict[str, Any]:
    fars = fars or [0.001, 0.01, 0.1]
    scores: list[float] = []
    for item in pairs:
        score_val = item.get("score")
        if score_val is None:
            score_val = item.get("similarity")
        if score_val is None:
            score_val = 0.0
        scores.append(float(score_val))
    labels = [1 if bool(item.get("label", item.get("same", False))) else 0 for item in pairs]
    positives = sum(labels)
    negatives = len(labels) - positives
    return {
        "pair_count": len(pairs),
        "positive_count": positives,
        "negative_count": negatives,
        "roc_auc": roc_auc(scores, labels),
        "tar_at_far": [tar_at_far(scores, labels, far) for far in fars],
        "calibration": calibrate_thresholds(scores, labels),
        "score_separation": score_separation(scores, labels),
        "score_p50": percentile(scores, 0.50),
        "score_p95": percentile(scores, 0.95),
    }


def retrieval_identity(item: dict[str, Any]) -> str | None:
    for key in ("person_id", "identity", "gt_person_id", "label"):
        value = item.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return None


def retrieval_positive_ids(query: dict[str, Any]) -> set[str]:
    raw_values = query.get("positive_person_ids", query.get("positive_ids", query.get("positives")))
    if isinstance(raw_values, list):
        return {str(value) for value in raw_values if value is not None and str(value) != ""}
    identity = retrieval_identity(query)
    return {identity} if identity else set()


def retrieval_camera_id(item: dict[str, Any]) -> str | None:
    for key in ("camera_id", "camera", "cam_id"):
        value = item.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return None


def retrieval_candidate_score(candidate: dict[str, Any]) -> float:
    for key in ("score", "similarity", "template_similarity", "final_score"):
        if key in candidate:
            try:
                return float(candidate.get(key, 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def retrieval_candidate_is_match(candidate: dict[str, Any], positive_ids: set[str]) -> bool:
    for key in ("match", "same", "positive", "label"):
        if key in candidate and isinstance(candidate[key], bool):
            return bool(candidate[key])
    candidate_identity = retrieval_identity(candidate)
    return candidate_identity in positive_ids if candidate_identity is not None else False


def retrieval_dcg_at_k(relevance: list[int], k: int) -> float:
    total = 0.0
    for rank, rel in enumerate(relevance[: max(0, k)], start=1):
        if rel <= 0:
            continue
        total += 1.0 / math.log2(rank + 1)
    return total


def retrieval_ndcg_at_k(relevance: list[int], k: int) -> float | None:
    if not relevance:
        return None
    actual = retrieval_dcg_at_k(relevance, k)
    ideal = retrieval_dcg_at_k(sorted(relevance, reverse=True), k)
    if ideal <= 0:
        return None
    return actual / ideal


def retrieval_precision_recall_at_k(relevance: list[int], positive_count: int, k: int) -> tuple[float | None, float | None]:
    if positive_count <= 0 or not relevance:
        return None, None
    cutoff = min(max(0, k), len(relevance))
    if cutoff <= 0:
        return None, None
    retrieved_relevant = sum(1 for rel in relevance[:cutoff] if rel > 0)
    precision = retrieved_relevant / float(cutoff)
    recall = retrieved_relevant / float(positive_count)
    return precision, recall


def evaluate_retrieval(
    queries: list[dict[str, Any]],
    ranks: list[int] | None = None,
    *,
    exclude_same_camera: bool = True,
    junk_ids: list[str] | None = None,
) -> dict[str, Any]:
    ranks = sorted({rank for rank in (ranks or [1, 5, 10]) if rank > 0})
    junk_id_set = {item for item in (junk_ids or [])}
    valid_queries: list[dict[str, Any]] = []
    total_candidates = 0
    evaluated_candidates = 0
    ignored_candidates = 0

    for query_index, query in enumerate(queries):
        if not isinstance(query, dict) or not isinstance(query.get("candidates"), list):
            continue
        positive_ids = retrieval_positive_ids(query)
        if not positive_ids:
            continue

        query_camera = retrieval_camera_id(query)
        ranked_candidates = sorted(
            [candidate for candidate in query["candidates"] if isinstance(candidate, dict)],
            key=retrieval_candidate_score,
            reverse=True,
        )
        total_candidates += len(ranked_candidates)

        matches: list[bool] = []
        for candidate in ranked_candidates:
            candidate_identity = retrieval_identity(candidate)
            candidate_camera = retrieval_camera_id(candidate)
            is_match = retrieval_candidate_is_match(candidate, positive_ids)
            ignored = bool(candidate.get("ignore", False) or candidate.get("junk", False))
            ignored = ignored or (candidate_identity in junk_id_set if candidate_identity is not None else False)
            ignored = ignored or (
                exclude_same_camera
                and is_match
                and query_camera is not None
                and candidate_camera == query_camera
            )
            if ignored:
                ignored_candidates += 1
                continue
            matches.append(is_match)

        evaluated_candidates += len(matches)
        positive_count = sum(1 for match in matches if match)
        if positive_count == 0:
            continue

        hit_count = 0
        precision_sum = 0.0
        first_hit_rank = None
        for rank_index, match in enumerate(matches, start=1):
            if not match:
                continue
            hit_count += 1
            if first_hit_rank is None:
                first_hit_rank = rank_index
            precision_sum += hit_count / rank_index

        average_precision = precision_sum / positive_count
        relevance = [1 if match else 0 for match in matches]
        ndcg_by_rank = {
            f"rank_{rank}": retrieval_ndcg_at_k(relevance, rank)
            for rank in ranks
        }
        precision_by_rank: dict[str, float | None] = {}
        recall_by_rank: dict[str, float | None] = {}
        for rank in ranks:
            precision, recall = retrieval_precision_recall_at_k(relevance, positive_count, rank)
            precision_by_rank[f"rank_{rank}"] = precision
            recall_by_rank[f"rank_{rank}"] = recall
        valid_queries.append(
            {
                "query_id": query.get("query_id", query.get("id", query_index)),
                "positive_count": positive_count,
                "evaluated_candidate_count": len(matches),
                "average_precision": average_precision,
                "first_hit_rank": first_hit_rank,
                "reciprocal_rank": (1.0 / first_hit_rank) if first_hit_rank else 0.0,
                "ndcg_at_k": ndcg_by_rank,
                "precision_at_k": precision_by_rank,
                "recall_at_k": recall_by_rank,
            }
        )

    valid_count = len(valid_queries)
    mean_ap = sum(item["average_precision"] for item in valid_queries) / valid_count if valid_count else None
    mean_rr = sum(item["reciprocal_rank"] for item in valid_queries) / valid_count if valid_count else None
    cmc = {
        f"rank_{rank}": (
            round(sum(1 for item in valid_queries if item["first_hit_rank"] is not None and item["first_hit_rank"] <= rank) / valid_count, 6)
            if valid_count
            else None
        )
        for rank in ranks
    }
    ndcg = {
        f"rank_{rank}": (
            round(
                sum(
                    float(item["ndcg_at_k"][f"rank_{rank}"])
                    for item in valid_queries
                    if item["ndcg_at_k"].get(f"rank_{rank}") is not None
                )
                / valid_count,
                6,
            )
            if valid_count
            else None
        )
        for rank in ranks
    }
    precision_at_k = {
        f"rank_{rank}": (
            round(
                sum(
                    float(item["precision_at_k"][f"rank_{rank}"])
                    for item in valid_queries
                    if item["precision_at_k"].get(f"rank_{rank}") is not None
                )
                / valid_count,
                6,
            )
            if valid_count
            else None
        )
        for rank in ranks
    }
    recall_at_k = {
        f"rank_{rank}": (
            round(
                sum(
                    float(item["recall_at_k"][f"rank_{rank}"])
                    for item in valid_queries
                    if item["recall_at_k"].get(f"rank_{rank}") is not None
                )
                / valid_count,
                6,
            )
            if valid_count
            else None
        )
        for rank in ranks
    }
    return {
        "query_count": len(queries),
        "valid_query_count": valid_count,
        "invalid_query_count": len(queries) - valid_count,
        "candidate_count": total_candidates,
        "evaluated_candidate_count": evaluated_candidates,
        "ignored_candidate_count": ignored_candidates,
        "map": round(mean_ap, 6) if mean_ap is not None else None,
        "mrr": round(mean_rr, 6) if mean_rr is not None else None,
        "cmc": cmc,
        "ndcg": ndcg,
        "precision_at_k": precision_at_k,
        "recall_at_k": recall_at_k,
        "mean_positive_count": round(
            sum(item["positive_count"] for item in valid_queries) / valid_count,
            6,
        ) if valid_count else None,
        "per_query": [
            {
                **item,
                "average_precision": round(float(item["average_precision"]), 6),
                "reciprocal_rank": round(float(item["reciprocal_rank"]), 6),
                "ndcg_at_k": {
                    key: round(float(value), 6) if value is not None else None
                    for key, value in item["ndcg_at_k"].items()
                },
                "precision_at_k": {
                    key: round(float(value), 6) if value is not None else None
                    for key, value in item["precision_at_k"].items()
                },
                "recall_at_k": {
                    key: round(float(value), 6) if value is not None else None
                    for key, value in item["recall_at_k"].items()
                },
            }
            for item in valid_queries
        ],
    }


def greedy_match_frame(
    ground_truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    iou_threshold: float,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], float]], int, int]:
    candidates: list[tuple[float, int, int]] = []
    for gt_index, gt in enumerate(ground_truth):
        for pred_index, pred in enumerate(predictions):
            iou = box_iou(gt.get("box", []), pred.get("box", []))
            if iou >= iou_threshold:
                candidates.append((iou, gt_index, pred_index))
    candidates.sort(reverse=True)
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matches = []
    for iou, gt_index, pred_index in candidates:
        if gt_index in used_gt or pred_index in used_pred:
            continue
        used_gt.add(gt_index)
        used_pred.add(pred_index)
        matches.append((ground_truth[gt_index], predictions[pred_index], iou))
    return matches, len(ground_truth) - len(used_gt), len(predictions) - len(used_pred)


def evaluate_tracking(frames: list[dict[str, Any]], iou_threshold: float = 0.5) -> dict[str, Any]:
    total_gt = 0
    total_matches = 0
    total_misses = 0
    total_false_positives = 0
    id_switches = 0
    gt_to_track: dict[str, str] = {}
    gt_frame_counts: dict[str, int] = {}
    gt_matched_counts: dict[str, int] = {}

    for frame in frames:
        ground_truth = frame.get("ground_truth", [])
        predictions = frame.get("predictions", frame.get("persons", []))
        if not isinstance(ground_truth, list) or not isinstance(predictions, list):
            continue
        total_gt += len(ground_truth)
        for gt in ground_truth:
            if not isinstance(gt, dict):
                continue
            gt_id = str(gt.get("id", gt.get("person_id", "")))
            if gt_id:
                gt_frame_counts[gt_id] = gt_frame_counts.get(gt_id, 0) + 1
        matches, misses, false_positives = greedy_match_frame(ground_truth, predictions, iou_threshold)
        total_matches += len(matches)
        total_misses += misses
        total_false_positives += false_positives
        for gt, pred, _ in matches:
            gt_id = str(gt.get("id", gt.get("person_id", "")))
            track_id = str(pred.get("track_id", ""))
            if not gt_id or not track_id:
                continue
            gt_matched_counts[gt_id] = gt_matched_counts.get(gt_id, 0) + 1
            previous_track = gt_to_track.get(gt_id)
            if previous_track is not None and previous_track != track_id:
                id_switches += 1
            gt_to_track[gt_id] = track_id

    mota = 1.0 - (total_misses + total_false_positives + id_switches) / total_gt if total_gt else None
    id_precision_denominator = total_matches + total_false_positives + id_switches
    id_recall_denominator = total_matches + total_misses + id_switches
    id_precision = total_matches / id_precision_denominator if id_precision_denominator else None
    id_recall = total_matches / id_recall_denominator if id_recall_denominator else None
    idf1 = (
        2 * id_precision * id_recall / (id_precision + id_recall)
        if id_precision is not None and id_recall is not None and (id_precision + id_recall) > 0
        else None
    )
    detection_accuracy_denominator = total_matches + total_misses + total_false_positives
    detection_accuracy = total_matches / detection_accuracy_denominator if detection_accuracy_denominator else None
    association_accuracy = max(0.0, 1.0 - id_switches / max(1, total_matches)) if total_matches else None
    hota_proxy = (
        (detection_accuracy * association_accuracy) ** 0.5
        if detection_accuracy is not None and association_accuracy is not None
        else None
    )
    track_coverages = [
        gt_matched_counts.get(gt_id, 0) / total
        for gt_id, total in gt_frame_counts.items()
        if total > 0
    ]
    return {
        "frame_count": len(frames),
        "ground_truth_count": total_gt,
        "matches": total_matches,
        "misses": total_misses,
        "false_positives": total_false_positives,
        "id_switches": id_switches,
        "mota": round(mota, 6) if mota is not None else None,
        "id_precision": round(id_precision, 6) if id_precision is not None else None,
        "id_recall": round(id_recall, 6) if id_recall is not None else None,
        "idf1": round(idf1, 6) if idf1 is not None else None,
        "detection_accuracy": round(detection_accuracy, 6) if detection_accuracy is not None else None,
        "association_accuracy": round(association_accuracy, 6) if association_accuracy is not None else None,
        "hota_proxy": round(hota_proxy, 6) if hota_proxy is not None else None,
        "track_coverage_p50": percentile(track_coverages, 0.50),
        "mostly_tracked_count": sum(1 for coverage in track_coverages if coverage >= 0.80),
        "mostly_lost_count": sum(1 for coverage in track_coverages if coverage <= 0.20),
    }


def extract_quality_scores(payload: Any) -> list[float]:
    scores: list[float] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("quality"), dict) and "score" in payload["quality"]:
            scores.append(float(payload["quality"]["score"]))
        elif "score" in payload and isinstance(payload["score"], (int, float)) and any(
            key in payload for key in ["sharpness", "blur", "exposure", "size_score"]
        ):
            scores.append(float(payload["score"]))
        for key, value in payload.items():
            if key == "quality":
                continue
            scores.extend(extract_quality_scores(value))
    elif isinstance(payload, list):
        for item in payload:
            scores.extend(extract_quality_scores(item))
    return scores


def evaluate_quality(payload: Any) -> dict[str, Any]:
    scores = extract_quality_scores(payload)
    return {
        "quality_count": len(scores),
        "quality_p05": percentile(scores, 0.05),
        "quality_p50": percentile(scores, 0.50),
        "quality_p95": percentile(scores, 0.95),
        "quality_min": round(min(scores), 6) if scores else None,
        "quality_max": round(max(scores), 6) if scores else None,
    }


def run_evaluation(manifest: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {"ok": True, "metrics": {}}
    if isinstance(manifest.get("pairs"), list):
        report["metrics"]["compare"] = evaluate_pairs(manifest["pairs"], manifest.get("fars"))
    retrieval = manifest.get("retrieval", manifest.get("gallery_retrieval"))
    if isinstance(retrieval, dict) and isinstance(retrieval.get("queries"), list):
        report["metrics"]["retrieval"] = evaluate_retrieval(
            retrieval["queries"],
            retrieval.get("ranks"),
            exclude_same_camera=bool(retrieval.get("exclude_same_camera", True)),
            junk_ids=retrieval.get("junk_ids") if isinstance(retrieval.get("junk_ids"), list) else None,
        )
    tracking = manifest.get("tracking")
    if isinstance(tracking, dict) and isinstance(tracking.get("frames"), list):
        report["metrics"]["tracking"] = evaluate_tracking(tracking["frames"], float(tracking.get("iou_threshold", 0.5)))
    quality_payload = manifest.get("quality", manifest.get("frames"))
    if quality_payload is not None:
        report["metrics"]["quality"] = evaluate_quality(quality_payload)
    report["ok"] = bool(report["metrics"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate PortraitHub algorithm outputs offline.")
    parser.add_argument("--manifest", required=True, help="Evaluation manifest YAML/JSON.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report = run_evaluation(load_manifest(Path(args.manifest).resolve()))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"人像算法评估： {'通过' if report['ok'] else '失败'}")
        for name, metrics in report["metrics"].items():
            print(f"{name}: {json.dumps(metrics, ensure_ascii=False, sort_keys=True)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
