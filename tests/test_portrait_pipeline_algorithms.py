from typing import Any

from PIL import Image

from app.geometry import person_crop_quality
from app.media.frame_sampler import hybrid_sample_indexes
from app.portrait_embeddings import best_face_embedding
from app.portrait_compare import (
    apply_input_independence_to_decision,
    compare_track_templates,
    fuse_modalities,
    quality_aware_compare,
)
from app.portrait_gallery import GALLERY, add_feature, aggregate_gallery_candidates, search_gallery, upsert_person
from app.portrait_tracking import aggregate_track_template, associate_person_tracks, box_iou
import app.portrait_vector_store as portrait_vector_store
from app.video_io import derive_scene_segments, scene_change_at, select_quality_diverse_positions


def test_hybrid_sampling_covers_video_span() -> None:
    indexes = hybrid_sample_indexes(total_frames=100, frame_interval=5, max_frames=5)

    assert indexes[0] == 0
    assert indexes[-1] == 95
    assert len(indexes) == 5


def test_quality_diverse_frame_selection_keeps_ordered_best_candidates() -> None:
    positions = select_quality_diverse_positions(
        qualities=[
            {"score": 0.1, "size_score": 1.0},
            {"score": 0.9, "size_score": 1.0},
            {"score": 0.2, "size_score": 1.0},
            {"score": 0.8, "size_score": 1.0},
            {"score": 0.3, "size_score": 1.0},
        ],
        scene_change_scores=[0.0, 0.1, 0.2, 0.8, 0.1],
        source_frame_indexes=[0, 10, 20, 30, 40],
        max_frames=2,
    )

    assert positions == sorted(positions)
    assert positions == [1, 3]


def test_quality_diverse_frame_selection_suppresses_near_duplicate_hashes() -> None:
    positions = select_quality_diverse_positions(
        qualities=[
            {"score": 0.95, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.94, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.60, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.55, "size_score": 1.0, "contrast": 0.5},
        ],
        scene_change_scores=[0.0, 0.0, 0.0, 0.0],
        source_frame_indexes=[0, 10, 20, 30],
        max_frames=2,
        fingerprints=[
            {"average_hash": "0000000000000000", "difference_hash": "0000000000000000"},
            {"average_hash": "0000000000000000", "difference_hash": "0000000000000000"},
            {"average_hash": "ffffffffffffffff", "difference_hash": "ffffffffffffffff"},
            {"average_hash": "0f0f0f0f0f0f0f0f", "difference_hash": "0f0f0f0f0f0f0f0f"},
        ],
    )

    assert positions == [0, 2]


def test_quality_diverse_frame_selection_keeps_temporal_coverage() -> None:
    source_frame_indexes = [0, 10, 20, 30, 90, 100]
    positions = select_quality_diverse_positions(
        qualities=[
            {"score": 0.91, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.90, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.89, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.88, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.72, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.70, "size_score": 1.0, "contrast": 0.5},
        ],
        scene_change_scores=[0.0, 0.0, 0.0, 0.0, 0.15, 0.20],
        source_frame_indexes=source_frame_indexes,
        max_frames=3,
    )

    assert positions == sorted(positions)
    assert max(source_frame_indexes[position] for position in positions) >= 90


def test_quality_diverse_frame_selection_covers_scene_segments() -> None:
    positions = select_quality_diverse_positions(
        qualities=[
            {"score": 0.95, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.94, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.93, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.72, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.70, "size_score": 1.0, "contrast": 0.5},
            {"score": 0.68, "size_score": 1.0, "contrast": 0.5},
        ],
        scene_change_scores=[0.0, 0.04, 0.03, 0.86, 0.05, 0.04],
        source_frame_indexes=[0, 12, 24, 72, 84, 96],
        max_frames=2,
    )

    assert positions == sorted(positions)
    assert any(position < 3 for position in positions)
    assert any(position >= 3 for position in positions)


def test_scene_segment_helpers_handle_short_scene_change_lists() -> None:
    assert scene_change_at([0.25], 3) == 0.0
    assert derive_scene_segments([0.0], [0, 10, 20, 30]) == [0, 0, 0, 0]


def test_person_crop_quality_reports_geometry_and_usability() -> None:
    image = Image.new("RGB", (100, 200), color=(120, 120, 120))

    quality = person_crop_quality(image, [25, 20, 75, 180])
    tiny = person_crop_quality(image, [10, 10, 10.5, 11])

    assert quality["usable"] is True
    assert quality["area_ratio"] == 0.4
    assert quality["truncation"] == 0.0
    assert tiny["usable"] is False
    assert tiny["reason"] == "crop_too_small"


def test_tracking_associates_persons_across_frames_with_embedding() -> None:
    embedding = [1.0, 0.0, 0.0]
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {
                    "box": [10, 10, 50, 90],
                    "score": 0.8,
                    "quality": {"score": 0.7},
                    "_tracking_embedding": embedding,
                }
            ],
        },
        {
            "frame_index": 1,
            "persons": [
                {
                    "box": [12, 12, 52, 92],
                    "score": 0.7,
                    "quality": {"score": 0.72},
                    "_tracking_embedding": [0.99, 0.01, 0.0],
                }
            ],
        },
    ]

    result = associate_person_tracks(frames)

    assert result["track_count"] == 1
    assert frames[0]["persons"][0]["track_id"] == frames[1]["persons"][0]["track_id"]
    assert frames[1]["persons"][0]["track_state"] == "tracked"
    assert result["tracks"][0]["template"]["sample_count"] == 2
    assert "embedding" not in result["tracks"][0]["template"]


def test_tracking_template_can_include_quality_weighted_embedding() -> None:
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {"box": [0, 0, 20, 40], "score": 0.8, "quality": {"score": 0.9}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
        {
            "frame_index": 1,
            "persons": [
                {"box": [1, 0, 21, 40], "score": 0.7, "quality": {"score": 0.7}, "_tracking_embedding": [0.9, 0.1]}
            ],
        },
    ]

    result = associate_person_tracks(frames, include_template_embeddings=True)
    template = result["tracks"][0]["template"]

    assert template["sample_count"] == 2
    assert template["embedding_dim"] == 2
    assert template["quality"] > 0.7
    assert "embedding" in template
    assert result["tracks"][0]["tracklet_quality_score"] > 0


def test_tracking_template_downweights_unusable_crop_samples() -> None:
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {
                    "box": [0, 0, 20, 40],
                    "score": 0.9,
                    "quality": {"score": 0.9},
                    "crop_quality": {"score": 0.9, "usable": True},
                    "_tracking_embedding": [1.0, 0.0],
                }
            ],
        },
        {
            "frame_index": 1,
            "persons": [
                {
                    "box": [1, 0, 21, 40],
                    "score": 0.9,
                    "quality": {"score": 0.2},
                    "crop_quality": {"score": 0.05, "usable": False},
                    "_tracking_embedding": [0.0, 1.0],
                }
            ],
        },
    ]

    result = associate_person_tracks(frames, include_template_embeddings=True)
    embedding = result["tracks"][0]["template"]["embedding"]

    assert embedding[0] > embedding[1]
    assert result["tracks"][0]["template"]["quality"] < 0.9


def test_track_template_reweights_outlier_embeddings() -> None:
    template = aggregate_track_template(
        [
            {
                "frame_index": 0,
                "embedding": [1.0, 0.0],
                "quality": 0.4,
                "crop_quality": 0.4,
                "crop_usable": True,
                "confidence": 0.4,
            },
            {
                "frame_index": 1,
                "embedding": [0.96, 0.28],
                "quality": 0.4,
                "crop_quality": 0.4,
                "crop_usable": True,
                "confidence": 0.4,
            },
            {
                "frame_index": 10,
                "embedding": [0.0, -1.0],
                "quality": 1.0,
                "crop_quality": 1.0,
                "crop_usable": True,
                "confidence": 1.0,
            },
        ],
        include_embedding=True,
    )

    assert template["sample_count"] == 3
    assert template["embedding"][0] > template["embedding"][1]
    assert template["robustness"]["method"] == "pairwise_consensus_reweighting"
    assert template["robustness"]["outlier_count"] == 1
    assert template["robustness"]["consensus_score"] < 0.9
    assert template["temporal"]["method"] == "linear_recency_decay"
    assert template["temporal"]["frame_span"] == [0, 10]
    assert template["temporal"]["recency_p95"] <= 1.0


def test_tracking_interpolates_short_gaps_and_smooths_boxes() -> None:
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {"box": [0, 0, 10, 20], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
        {
            "frame_index": 2,
            "persons": [
                {"box": [2, 0, 12, 20], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [0.99, 0.01]}
            ],
        },
    ]

    result = associate_person_tracks(frames, max_age=2)

    assert result["tracks"][0]["interpolated_count"] == 1
    assert result["tracks"][0]["interpolated"][0]["frame_index"] == 1
    assert "smoothed_box" in frames[0]["persons"][0]


def test_tracking_uses_velocity_prediction_for_fast_motion() -> None:
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {"box": [0, 0, 10, 10], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
        {
            "frame_index": 1,
            "persons": [
                {"box": [10, 0, 20, 10], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
        {
            "frame_index": 2,
            "persons": [
                {"box": [20, 0, 30, 10], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
    ]

    result = associate_person_tracks(frames, min_match_score=0.25)

    assert result["track_count"] == 1
    assert frames[2]["persons"][0]["track_match"]["last_iou"] == 0.0
    assert frames[2]["persons"][0]["track_match"]["predicted_iou"] == 1.0


def test_tracking_marks_appearance_rescue_association_risk() -> None:
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {"box": [0, 0, 10, 10], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
        {
            "frame_index": 1,
            "persons": [
                {"box": [50, 0, 60, 10], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
    ]

    result = associate_person_tracks(frames, min_match_score=0.5)
    decision = frames[1]["persons"][0]["track_match"]["decision"]
    association_quality = result["tracks"][0]["association_quality"]

    assert result["track_count"] == 1
    assert decision["risk"] == "appearance_rescue"
    assert "appearance" in decision["supporting_signals"]
    assert association_quality["risky_match_count"] == 1
    assert association_quality["dominant_risk"] == "appearance_rescue"


def test_tracking_uses_global_assignment_instead_of_local_greedy_choice() -> None:
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {"box": [0, 0, 10, 10], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]},
                {"box": [100, 0, 110, 10], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [0.0, 1.0]},
            ],
        },
        {
            "frame_index": 1,
            "persons": [
                {
                    "box": [0, 0, 10, 10],
                    "score": 0.9,
                    "quality": {"score": 0.8},
                    "_tracking_embedding": [0.0, 1.0],
                },
                {
                    "box": [50, 0, 60, 10],
                    "score": 0.9,
                    "quality": {"score": 0.8},
                    "_tracking_embedding": [1.0, 0.0],
                },
            ],
        },
    ]

    result = associate_person_tracks(frames, min_match_score=0.5)

    assert result["track_count"] == 2
    assert frames[1]["persons"][0]["track_id"] == frames[0]["persons"][1]["track_id"]
    assert frames[1]["persons"][1]["track_id"] == frames[0]["persons"][0]["track_id"]
    assert frames[1]["persons"][0]["track_match"]["association_solver"] == "global_optimal_assignment"
    assert frames[1]["persons"][1]["track_match"]["association_solver"] == "global_optimal_assignment"


def test_tracking_merges_short_high_appearance_fragments() -> None:
    frames = [
        {
            "frame_index": 0,
            "persons": [
                {"box": [0, 0, 20, 40], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [1.0, 0.0]}
            ],
        },
        {"frame_index": 1, "persons": []},
        {"frame_index": 2, "persons": []},
        {"frame_index": 3, "persons": []},
        {
            "frame_index": 4,
            "persons": [
                {"box": [1, 0, 21, 40], "score": 0.9, "quality": {"score": 0.8}, "_tracking_embedding": [0.99, 0.01]}
            ],
        },
    ]

    result = associate_person_tracks(frames, max_age=2, max_fragment_merge_gap=4)

    assert result["track_count"] == 1
    assert result["fragment_merge"]["merge_count"] == 1
    assert frames[4]["persons"][0]["track_id"] == frames[0]["persons"][0]["track_id"]
    assert frames[4]["persons"][0]["track_fragment_merged_from"] == "trk_0002"
    assert result["tracks"][0]["frame_count"] == 2


def test_box_iou_handles_overlap() -> None:
    assert 0.0 < box_iou([0, 0, 10, 10], [5, 5, 15, 15]) < 1.0


def test_quality_aware_compare_keeps_raw_similarity_and_adds_adjustment() -> None:
    comparison = quality_aware_compare(
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        modality="face",
        threshold_profile="normal",
        quality_a=0.2,
        quality_b=0.2,
    )

    assert comparison["similarity"] == 1.0
    assert comparison["quality_adjusted_threshold"] > comparison["threshold"]
    assert "quality_gate" in comparison
    assert comparison["decision"]["margin"] > 0
    assert comparison["decision"]["risk"] == "low_quality"
    assert 0 <= comparison["decision"]["confidence"] <= 1


def test_low_texture_face_fallback_skips_expensive_haar_detection() -> None:
    image = Image.new("RGB", (96, 80), color=(100, 40, 40))

    embedding, face = best_face_embedding(image)

    assert len(embedding) == 64
    assert face["detection_strategy"] == "whole_image_fallback"


def test_quality_aware_compare_rejects_unusable_quality_even_when_similarity_is_high() -> None:
    comparison = quality_aware_compare(
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        modality="face",
        threshold_profile="normal",
        quality_a=0.05,
        quality_b=0.05,
    )

    assert comparison["similarity"] == 1.0
    assert comparison["quality_gate"]["usable"] is False
    assert comparison["passed"] is False
    assert comparison["decision"]["risk"] == "quality_unusable"
    assert comparison["decision"]["confidence"] == 0.0


def test_track_template_compare_uses_quality_aware_contract() -> None:
    comparison = compare_track_templates(
        {"embedding": [1.0, 0.0], "quality": 0.8, "confidence": 0.9, "sample_count": 3},
        {"embedding": [0.99, 0.01], "quality": 0.7, "confidence": 0.8, "sample_count": 2},
        threshold_profile="normal",
    )
    missing = compare_track_templates({"quality": 0.8}, {"embedding": [1.0, 0.0]})

    assert comparison["comparison_type"] == "track_template"
    assert comparison["passed"] is True
    assert comparison["decision"]["risk"] == "clear"
    assert comparison["template_a"]["sample_count"] == 3
    assert missing["reason"] == "template_embedding_missing"


def test_quality_aware_compare_can_use_frame_quality_signal() -> None:
    comparison = quality_aware_compare(
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        modality="body",
        threshold_profile="normal",
        quality_a=0.15,
        quality_b=0.15,
    )

    assert comparison["quality_gate"]["score"] == 0.15
    assert comparison["passed"] is True
    assert comparison["decision"]["risk"] == "low_quality"


def test_fusion_reports_consistency_and_conflict_penalty() -> None:
    conflicted = fuse_modalities(
        {
            "face": {"score": 0.95, "quality": 0.9},
            "body": {"score": 0.30, "quality": 0.9},
            "appearance": {"score": 0.90, "quality": 0.9},
        }
    )
    consistent = fuse_modalities(
        {
            "face": {"score": 0.86, "quality": 0.9},
            "body": {"score": 0.82, "quality": 0.9},
            "appearance": {"score": 0.84, "quality": 0.9},
        }
    )

    assert conflicted["consistency"]["conflict_penalty"] > 0
    assert conflicted["final_score"] < conflicted["raw_score"]
    assert conflicted["decision"]["risk"] == "modality_conflict"
    assert consistent["consistency"]["conflict_penalty"] == 0
    assert consistent["decision"]["margin"] > 0


def test_input_independence_preserves_existing_risk_factors() -> None:
    payload = {
        "decision": {
            "confidence": 0.8,
            "risk": "modality_conflict",
            "risk_factors": ["score_gap"],
        }
    }

    apply_input_independence_to_decision(
        payload,
        {"exact_duplicate": False, "near_duplicate": True},
    )

    assert payload["decision"]["risk"] == "modality_conflict"
    assert payload["decision"]["confidence"] == 0.52
    assert payload["decision"]["input_independence"]["risk"] == "near_duplicate_input"
    assert payload["decision"]["risk_factors"] == ["score_gap", "modality_conflict", "near_duplicate_input"]


def test_gallery_candidates_are_aggregated_by_person() -> None:
    candidates = [
        {
            "tenant_id": "tenant-a",
            "person_id": "p1",
            "display_name": "A",
            "similarity": 0.81,
            "threshold": 0.7,
            "feature": {"feature_id": "f1", "quality_score": 0.4},
        },
        {
            "tenant_id": "tenant-a",
            "person_id": "p1",
            "display_name": "A",
            "similarity": 0.79,
            "threshold": 0.7,
            "feature": {"feature_id": "f2", "quality_score": 0.9},
        },
        {
            "tenant_id": "tenant-a",
            "person_id": "p2",
            "display_name": "B",
            "similarity": 0.65,
            "threshold": 0.7,
            "feature": {"feature_id": "f3", "quality_score": 0.9},
        },
    ]

    aggregated = aggregate_gallery_candidates(candidates, top_k=5)

    assert len(aggregated) == 2
    assert aggregated[0]["person_id"] == "p1"
    assert aggregated[0]["supporting_feature_count"] == 2
    assert aggregated[0]["feature"]["feature_id"] in {"f1", "f2"}
    assert aggregated[0]["template_quality"] > 0
    assert aggregated[0]["decision"]["margin"] > 0
    assert 0 <= aggregated[0]["decision"]["confidence"] <= 1


def test_gallery_candidate_decision_flags_low_quality_templates() -> None:
    aggregated = aggregate_gallery_candidates(
        [
            {
                "tenant_id": "tenant-a",
                "person_id": "p-low",
                "display_name": "Low",
                "similarity": 0.72,
                "threshold": 0.7,
                "feature": {"feature_id": "f-low", "quality_score": 0.05},
            }
        ],
        top_k=5,
    )

    assert aggregated[0]["decision"]["risk"] == "low_template_quality"
    assert aggregated[0]["template_quality"] == 0.05


def test_gallery_candidates_preserve_template_risk_under_low_query_quality() -> None:
    aggregated = aggregate_gallery_candidates(
        [
            {
                "tenant_id": "tenant-a",
                "person_id": "p-low",
                "display_name": "Low",
                "similarity": 0.72,
                "threshold": 0.7,
                "feature": {"feature_id": "f-low", "quality_score": 0.05},
            }
        ],
        top_k=5,
        query_quality=0.18,
    )

    decision = aggregated[0]["decision"]

    assert decision["risk"] == "low_template_quality"
    assert decision["query_quality"]["risk"] == "low_query_quality"
    assert decision["query_quality"]["usable"] is True
    assert "low_template_quality" in decision["risk_factors"]
    assert "low_query_quality" in decision["risk_factors"]
    assert decision["confidence"] < 0.5


def test_gallery_rank_ambiguity_outweighs_weak_query_quality() -> None:
    aggregated = aggregate_gallery_candidates(
        [
            {
                "tenant_id": "tenant-a",
                "person_id": "p1",
                "display_name": "A",
                "similarity": 0.812,
                "threshold": 0.7,
                "feature": {"feature_id": "f1", "quality_score": 0.9},
            },
            {
                "tenant_id": "tenant-a",
                "person_id": "p2",
                "display_name": "B",
                "similarity": 0.805,
                "threshold": 0.7,
                "feature": {"feature_id": "f2", "quality_score": 0.9},
            },
        ],
        top_k=5,
        query_quality=0.30,
    )

    decision = aggregated[0]["decision"]

    assert decision["risk"] == "rank_ambiguous"
    assert decision["query_quality"]["risk"] == "weak_query_quality"
    assert "weak_query_quality" in decision["risk_factors"]
    assert "rank_ambiguous" in decision["risk_factors"]


def test_gallery_candidates_mark_unusable_query_quality_as_primary_risk() -> None:
    aggregated = aggregate_gallery_candidates(
        [
            {
                "tenant_id": "tenant-a",
                "person_id": "p1",
                "display_name": "A",
                "similarity": 0.88,
                "threshold": 0.7,
                "feature": {"feature_id": "f1", "quality_score": 0.9},
            }
        ],
        top_k=5,
        query_quality=0.05,
    )

    decision = aggregated[0]["decision"]

    assert decision["risk"] == "query_quality_unusable"
    assert decision["query_quality"]["usable"] is False
    assert "query_quality_unusable" in decision["risk_factors"]
    assert decision["confidence"] <= 0.25


def test_gallery_candidates_mark_rank_ambiguity() -> None:
    aggregated = aggregate_gallery_candidates(
        [
            {
                "tenant_id": "tenant-a",
                "person_id": "p1",
                "display_name": "A",
                "similarity": 0.812,
                "threshold": 0.7,
                "feature": {"feature_id": "f1", "quality_score": 0.9},
            },
            {
                "tenant_id": "tenant-a",
                "person_id": "p2",
                "display_name": "B",
                "similarity": 0.805,
                "threshold": 0.7,
                "feature": {"feature_id": "f2", "quality_score": 0.9},
            },
        ],
        top_k=5,
    )

    assert aggregated[0]["rank_context"]["ambiguity_risk"] == "rank_ambiguous"
    assert aggregated[0]["rank_context"]["closest_competitor_person_id"] == "p2"
    assert aggregated[0]["decision"]["risk"] == "rank_ambiguous"
    assert "rank_ambiguous" in aggregated[0]["decision"]["risk_factors"]


def test_gallery_aggregation_returns_person_level_top_k_after_feature_pooling() -> None:
    candidates = [
        {
            "tenant_id": "tenant-a",
            "person_id": "p1",
            "display_name": "A",
            "similarity": 0.90 - index * 0.01,
            "threshold": 0.7,
            "feature": {"feature_id": f"f1_{index}", "quality_score": 0.9},
        }
        for index in range(5)
    ]
    candidates.extend(
        [
            {
                "tenant_id": "tenant-a",
                "person_id": "p2",
                "display_name": "B",
                "similarity": 0.78,
                "threshold": 0.7,
                "feature": {"feature_id": "f2", "quality_score": 0.8},
            },
            {
                "tenant_id": "tenant-a",
                "person_id": "p3",
                "display_name": "C",
                "similarity": 0.76,
                "threshold": 0.7,
                "feature": {"feature_id": "f3", "quality_score": 0.8},
            },
        ]
    )

    aggregated = aggregate_gallery_candidates(candidates, top_k=3)

    assert [candidate["person_id"] for candidate in aggregated] == ["p1", "p2", "p3"]
    assert aggregated[0]["supporting_feature_count"] == 5


def test_gallery_search_uses_query_expansion_for_two_stage_retrieval(monkeypatch) -> None:
    class FakeVectorStore:
        backend_name = "fake_two_stage"

        def search(
            self,
            query_embedding: list[float],
            records: list[dict[str, Any]],
            *,
            modality: str,
            threshold_profile: str,
            top_k: int,
            tenant_id: str = "default",
        ) -> list[dict[str, Any]]:
            query_bias = float(query_embedding[1]) if len(query_embedding) > 1 else 0.0
            if query_bias < 0.5:
                return [
                    {
                        "tenant_id": tenant_id,
                        "person_id": "p_seed",
                        "display_name": "Seed",
                        "feature": {"feature_id": feature_seed.feature_id, "quality_score": 0.95},
                        "modality": modality,
                        "similarity": 0.96,
                        "threshold": 0.7,
                        "threshold_profile": threshold_profile,
                        "passed": True,
                    },
                    {
                        "tenant_id": tenant_id,
                        "person_id": "p_comp",
                        "display_name": "Comp",
                        "feature": {"feature_id": feature_comp.feature_id, "quality_score": 0.90},
                        "modality": modality,
                        "similarity": 0.90,
                        "threshold": 0.7,
                        "threshold_profile": threshold_profile,
                        "passed": True,
                    },
                ][:top_k]
            return [
                {
                    "tenant_id": tenant_id,
                    "person_id": "p_recovered",
                    "display_name": "Recovered",
                    "feature": {"feature_id": feature_recovered.feature_id, "quality_score": 0.96},
                    "modality": modality,
                    "similarity": 0.99,
                    "threshold": 0.7,
                    "threshold_profile": threshold_profile,
                    "passed": True,
                },
                {
                    "tenant_id": tenant_id,
                    "person_id": "p_comp",
                    "display_name": "Comp",
                    "feature": {"feature_id": feature_comp.feature_id, "quality_score": 0.90},
                    "modality": modality,
                    "similarity": 0.81,
                    "threshold": 0.7,
                    "threshold_profile": threshold_profile,
                    "passed": True,
                },
            ][:top_k]

    GALLERY.clear()
    person_seed = upsert_person("p_seed", "Seed", tenant_id="tenant-a")
    feature_seed = add_feature(
        person_seed,
        modality="body",
        embedding=[0.2, 0.98],
        model_id="model",
        model_version="1.0",
        quality_score=0.95,
        source_id="seed_source",
    )
    person_comp = upsert_person("p_comp", "Comp", tenant_id="tenant-a")
    feature_comp = add_feature(
        person_comp,
        modality="body",
        embedding=[0.0, 1.0],
        model_id="model",
        model_version="1.0",
        quality_score=0.90,
        source_id="comp_source",
    )
    person_recovered = upsert_person("p_recovered", "Recovered", tenant_id="tenant-a")
    feature_recovered = add_feature(
        person_recovered,
        modality="body",
        embedding=[0.0, 1.0],
        model_id="model",
        model_version="1.0",
        quality_score=0.96,
        source_id="recovered_source",
    )

    monkeypatch.setattr(portrait_vector_store, "VECTOR_STORE", FakeVectorStore())

    candidates = search_gallery(
        [1.0, 0.0],
        modality="body",
        threshold_profile="normal",
        top_k=2,
        tenant_id="tenant-a",
        query_quality=0.82,
    )

    assert candidates[0]["person_id"] == "p_recovered"
    assert candidates[0]["retrieval_context"]["strategy"] == "two_stage_query_expansion"
    assert candidates[0]["retrieval_context"]["query_expansion"]["enabled"] is True
    assert candidates[0]["retrieval_context"]["query_expansion"]["selected_feature_count"] >= 1
