from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app import portrait_audit, portrait_feedback
from app.portrait_access import clear_access_state
from app.server import app


@pytest.fixture
def analysis_client(workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(portrait_feedback, "PORTRAIT_FEEDBACK_STATE_PATH", workspace_tmp_path / "feedback.json")
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", workspace_tmp_path / "audit.jsonl")
    clear_access_state()
    portrait_feedback.reset_feedback_state()
    return TestClient(app)


def headers(tenant_id: str = "tenant-a") -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id, "X-Project-ID": "default"}


def response_data(response: Response) -> dict[str, Any]:
    assert response.status_code < 400, response.text
    return response.json()["data"]


def create_review_sample(
    client: TestClient,
    *,
    item_id: str,
    model_version_id: str,
    confidence: float,
) -> dict[str, Any]:
    payload = {
        "source_request_id": f"request-{item_id}",
        "source_item_id": item_id,
        "reason": "false_positive" if item_id.endswith("negative") else "false_negative",
        "risk_level": "high",
        "confidence": confidence,
        "model_id": "person-classifier",
        "model_version_id": model_version_id,
        "model_sha256": ("a" if model_version_id == "baseline-v1" else "b") * 64,
        "content_sha256": item_id.encode("utf-8").hex().ljust(64, "0")[:64],
        "proposed_labels": {"person": confidence >= 0.5},
    }
    return response_data(
        client.post("/v1/evaluation/review-samples", headers=headers(), json=payload)
    )["review_sample"]


def create_analysis_manifest(client: TestClient) -> dict[str, Any]:
    samples = [
        (create_review_sample(client, item_id="baseline-positive", model_version_id="baseline-v1", confidence=0.9), True),
        (create_review_sample(client, item_id="baseline-negative", model_version_id="baseline-v1", confidence=0.8), False),
        (create_review_sample(client, item_id="candidate-positive", model_version_id="candidate-v2", confidence=0.9), True),
        (create_review_sample(client, item_id="candidate-negative", model_version_id="candidate-v2", confidence=0.1), False),
    ]
    sample_ids = [sample["review_sample_id"] for sample, _label in samples]
    exported = response_data(
        client.post(
            "/v1/evaluation/review-samples/export",
            headers=headers(),
            json={"sample_ids": sample_ids},
        )
    )["annotation_export"]
    response_data(
        client.post(
            "/v1/evaluation/review-samples/import",
            headers=headers(),
            json={
                "annotation_export_id": exported["annotation_export_id"],
                "annotations": [
                    {"review_sample_id": sample["review_sample_id"], "labels": {"person": label}}
                    for sample, label in samples
                ],
            },
        )
    )
    return response_data(
        client.post(
            "/v1/evaluation/datasets",
            headers=headers(),
            json={
                "name": "person-release-evidence",
                "version": "1.0.0",
                "splits": {"test": sample_ids},
                "lineage": [exported["annotation_export_id"]],
            },
        )
    )["dataset_manifest"]


def analysis_payload(dataset_id: str, *, version: str = "1.0.0", minimum_sample_count: int = 2) -> dict[str, Any]:
    return {
        "name": "person-classifier-release",
        "version": version,
        "dataset_ids": [dataset_id],
        "label_key": "person",
        "baseline_model_version_id": "baseline-v1",
        "candidate_model_version_id": "candidate-v2",
        "current_threshold": 0.5,
        "threshold_candidates": [0.4, 0.5, 0.7],
        "minimum_sample_count": minimum_sample_count,
        "minimum_accuracy": 0.8,
        "minimum_f1": 0.8,
    }


def test_feedback_analysis_is_versioned_read_only_and_tenant_scoped(analysis_client: TestClient) -> None:
    manifest = create_analysis_manifest(analysis_client)
    report = response_data(
        analysis_client.post(
            "/v1/evaluation/feedback-analysis-reports",
            headers=headers(),
            json=analysis_payload(manifest["dataset_id"]),
        )
    )["analysis_report"]

    assert report["status"] == "completed"
    assert report["immutable"] is True
    assert len(report["sha256"]) == 64
    assert report["error_analysis"]["metrics"]["sample_count"] == 4
    assert report["error_analysis"]["errors_by_review_reason"] == {"false_positive": 1}
    assert report["threshold_recommendation"]["status"] == "available"
    assert report["threshold_recommendation"]["read_only"] is True
    assert report["threshold_recommendation"]["configuration_changed"] is False
    assert report["model_comparison"]["deltas"]["accuracy"] == 0.5
    assert report["release_candidate"]["decision"] == "recommend_release"
    assert report["release_candidate"]["human_approval_required"] is True
    assert report["evidence_summary"]["dataset_manifests"][0]["sha256"] == manifest["sha256"]
    assert len(report["evidence_summary"]["sample_evidence_sha256"]) == 64

    fetched = response_data(
        analysis_client.get(
            f"/v1/evaluation/feedback-analysis-reports/{report['analysis_report_id']}",
            headers=headers(),
        )
    )["analysis_report"]
    assert fetched["sha256"] == report["sha256"]

    duplicate = analysis_client.post(
        "/v1/evaluation/feedback-analysis-reports",
        headers=headers(),
        json=analysis_payload(manifest["dataset_id"]),
    )
    assert duplicate.status_code == 409
    assert analysis_client.get(
        f"/v1/evaluation/feedback-analysis-reports/{report['analysis_report_id']}",
        headers=headers("tenant-b"),
    ).status_code == 404
    other_tenant = response_data(
        analysis_client.get("/v1/evaluation/feedback-analysis-reports", headers=headers("tenant-b"))
    )
    assert other_tenant["count"] == 0


def test_feedback_analysis_reports_insufficient_data_without_a_release_conclusion(
    analysis_client: TestClient,
) -> None:
    manifest = create_analysis_manifest(analysis_client)
    report = response_data(
        analysis_client.post(
            "/v1/evaluation/feedback-analysis-reports",
            headers=headers(),
            json=analysis_payload(manifest["dataset_id"], version="1.0.1", minimum_sample_count=5),
        )
    )["analysis_report"]

    assert report["status"] == "insufficient_data"
    assert report["error_analysis"]["status"] == "insufficient_data"
    assert report["threshold_recommendation"]["status"] == "insufficient_data"
    assert report["threshold_recommendation"]["recommended_threshold"] is None
    assert report["model_comparison"]["status"] == "insufficient_data"
    assert report["release_candidate"]["status"] == "insufficient_data"
    assert report["release_candidate"]["decision"] == "hold"
    assert report["release_candidate"]["blocking_reasons"] == ["insufficient_data"]
