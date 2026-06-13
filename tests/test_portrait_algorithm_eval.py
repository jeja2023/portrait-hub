from tools.portrait_algorithm_eval import evaluate_pairs, evaluate_quality, evaluate_retrieval, evaluate_tracking, run_evaluation
from tools.portrait_model_regression import run_model_regression


def test_evaluate_pairs_reports_auc_and_tar() -> None:
    metrics = evaluate_pairs(
        [
            {"score": 0.95, "label": 1},
            {"score": 0.80, "label": 1},
            {"score": 0.20, "label": 0},
            {"score": 0.10, "label": 0},
        ],
        fars=[0.5],
    )

    assert metrics["roc_auc"] == 1.0
    assert metrics["tar_at_far"][0]["tar"] == 1.0
    assert metrics["calibration"]["best_f1"]["f1"] == 1.0
    assert metrics["calibration"]["eer"]["eer"] == 0.0
    assert metrics["calibration"]["recommended"]["policy"] == "maximize_youden_then_precision"
    assert metrics["calibration"]["review_band"]["count"] >= 1
    assert metrics["score_separation"]["mean_gap"] > 0


def test_evaluate_tracking_counts_id_switches() -> None:
    metrics = evaluate_tracking(
        [
            {
                "ground_truth": [{"id": "g1", "box": [0, 0, 10, 10]}],
                "predictions": [{"track_id": "t1", "box": [0, 0, 10, 10]}],
            },
            {
                "ground_truth": [{"id": "g1", "box": [1, 0, 11, 10]}],
                "predictions": [{"track_id": "t2", "box": [1, 0, 11, 10]}],
            },
        ]
    )

    assert metrics["matches"] == 2
    assert metrics["id_switches"] == 1
    assert metrics["idf1"] < 1.0
    assert metrics["association_accuracy"] == 0.5
    assert metrics["hota_proxy"] < 1.0
    assert metrics["track_coverage_p50"] == 1.0


def test_evaluate_retrieval_reports_map_cmc_and_same_camera_filter() -> None:
    metrics = evaluate_retrieval(
        [
            {
                "query_id": "q1",
                "person_id": "p1",
                "camera_id": "c1",
                "candidates": [
                    {"person_id": "p1", "camera_id": "c1", "template_similarity": 0.99},
                    {"person_id": "p2", "camera_id": "c2", "template_similarity": 0.91},
                    {"person_id": "p1", "camera_id": "c2", "template_similarity": 0.85},
                ],
            },
            {
                "query_id": "q2",
                "positive_person_ids": ["p3"],
                "camera_id": "c1",
                "candidates": [
                    {"person_id": "p3", "camera_id": "c2", "similarity": 0.96},
                    {"person_id": "p4", "camera_id": "c2", "similarity": 0.40},
                ],
            },
        ],
        ranks=[1, 2],
    )

    assert metrics["valid_query_count"] == 2
    assert metrics["ignored_candidate_count"] == 1
    assert metrics["cmc"]["rank_1"] == 0.5
    assert metrics["cmc"]["rank_2"] == 1.0
    assert metrics["ndcg"]["rank_1"] == 0.5
    assert metrics["ndcg"]["rank_2"] == 0.815465
    assert metrics["precision_at_k"]["rank_1"] == 0.5
    assert metrics["precision_at_k"]["rank_2"] == 0.5
    assert metrics["recall_at_k"]["rank_1"] == 0.5
    assert metrics["recall_at_k"]["rank_2"] == 1.0
    assert 0 < metrics["map"] < 1
    assert metrics["mrr"] == 0.75


def test_evaluate_quality_extracts_nested_scores() -> None:
    metrics = evaluate_quality(
        {
            "frames": [
                {"quality": {"score": 0.2}},
                {"persons": [{"quality": {"score": 0.8}}]},
            ]
        }
    )

    assert metrics["quality_count"] == 2
    assert metrics["quality_min"] == 0.2
    assert metrics["quality_max"] == 0.8


def test_run_evaluation_combines_sections() -> None:
    report = run_evaluation(
        {
            "pairs": [{"score": 0.9, "same": True}, {"score": 0.1, "same": False}],
            "retrieval": {
                "queries": [
                    {
                        "query_id": "q1",
                        "person_id": "p1",
                        "candidates": [{"person_id": "p1", "score": 0.9}],
                    }
                ]
            },
            "tracking": {
                "frames": [
                    {
                        "ground_truth": [{"id": "g1", "box": [0, 0, 10, 10]}],
                        "predictions": [{"track_id": "t1", "box": [0, 0, 10, 10]}],
                    }
                ]
            },
            "quality": [{"quality": {"score": 0.5}}],
        }
    )

    assert report["ok"] is True
    assert set(report["metrics"]) == {"compare", "retrieval", "tracking", "quality"}
    assert report["metrics"]["retrieval"]["ndcg"]["rank_1"] == 1.0
    assert report["metrics"]["retrieval"]["precision_at_k"]["rank_1"] == 1.0
    assert report["metrics"]["retrieval"]["recall_at_k"]["rank_1"] == 1.0


def test_model_regression_applies_metric_gates() -> None:
    report = run_model_regression(
        {
            "pairs": [{"score": 0.9, "same": True}, {"score": 0.1, "same": False}],
            "gates": [
                {"path": "metrics.compare.roc_auc", "min": 0.99},
                {"path": "metrics.compare.score_p50", "max": 0.6},
            ],
        }
    )

    assert report["ok"] is True
    assert report["gate_failures"] == []
