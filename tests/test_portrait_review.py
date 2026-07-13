from collections.abc import Iterator

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import portrait_review, routes_portrait_review
from main import app


@pytest.fixture(autouse=True)
def isolated_review_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    portrait_review.clear_review_state()
    monkeypatch.setattr(portrait_review, "save_review_state", lambda: None)
    monkeypatch.setattr(routes_portrait_review, "audit_event", lambda *args, **kwargs: None)
    yield
    portrait_review.clear_review_state()


def tenant_headers(tenant_id: str = "tenant-a") -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id}


def create_annotation(client: TestClient, tenant_id: str = "tenant-a", **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "job-001",
        "track_id": "track-7",
        "label": "false_positive",
        "reviewer": "operator-a",
        "note": "background reflection",
        "frame_index": 12,
        "evidence_ref": "job-001/frame-12",
    }
    payload.update(extra)
    response = client.post("/v1/evaluation/track-reviews", headers=tenant_headers(tenant_id), json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]["annotation"]


def test_track_review_annotations_are_tenant_scoped_and_filterable() -> None:
    client = TestClient(app)
    annotation = create_annotation(client)
    create_annotation(client, tenant_id="tenant-b", job_id="job-002", track_id="track-b", label="mismatch")

    assert annotation["annotation_id"].startswith("rev_")
    assert annotation["tenant_id"] == "tenant-a"
    assert annotation["label"] == "false_positive"

    same_tenant = client.get("/v1/evaluation/track-reviews", headers=tenant_headers(), params={"job_id": "job-001"})
    other_tenant = client.get("/v1/evaluation/track-reviews", headers=tenant_headers("tenant-b"))
    mismatch_only = client.get("/v1/evaluation/track-reviews", headers=tenant_headers(), params={"label": "mismatch"})

    assert same_tenant.status_code == 200
    assert same_tenant.json()["data"]["count"] == 1
    assert same_tenant.json()["data"]["annotations"][0]["track_id"] == "track-7"
    assert other_tenant.json()["data"]["annotations"][0]["tenant_id"] == "tenant-b"
    assert mismatch_only.json()["data"]["annotations"] == []

def test_evaluation_datasets_are_derived_from_tenant_review_annotations() -> None:
    client = TestClient(app)
    create_annotation(client, label="false_positive", track_id="track-7", evidence_ref="job-001/frame-12")
    create_annotation(client, label="confirmed", track_id="track-8", evidence_ref="job-001/frame-20")
    create_annotation(client, label="low_quality", track_id="track-9", evidence_ref="job-001/frame-25")
    create_annotation(client, tenant_id="tenant-b", label="mismatch", track_id="track-b", evidence_ref="job-b/frame-1")

    response = client.get("/v1/evaluation/datasets", headers=tenant_headers(), params={"limit": 20})
    other_tenant = client.get("/v1/evaluation/datasets", headers=tenant_headers("tenant-b"))

    assert response.status_code == 200
    payload = response.json()["data"]
    datasets = payload["datasets"]
    by_name = {dataset["name"]: dataset for dataset in datasets}
    assert payload["count"] == len(datasets)
    assert by_name["review_all_annotations"]["sample_count"] == 3
    assert by_name["review_attention_holdout"]["sample_count"] == 2
    assert by_name["review_confirmed_samples"]["purpose"] == "positive_control"
    assert by_name["review_low_quality_samples"]["evidence_index"][0]["evidence_ref"] == "job-001/frame-25"
    assert all(dataset["dataset_id"].startswith("eval_") for dataset in datasets)
    assert other_tenant.json()["data"]["datasets"][0]["sample_count"] == 1
    assert "track-7" not in other_tenant.text
    assert "track-8" not in other_tenant.text
    assert "track-9" not in other_tenant.text
    assert "job-001/frame" not in other_tenant.text

def test_threshold_recommendations_are_read_only_and_tenant_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        routes_portrait_review,
        "threshold_snapshot",
        lambda: {
            "body": {"normal": 0.68},
            "fusion": {"normal": 0.72},
            "appearance": {"normal": 0.58},
        },
    )

    empty = client.get("/v1/evaluation/threshold-recommendations", headers=tenant_headers())
    assert empty.status_code == 200
    empty_payload = empty.json()["data"]["threshold_recommendations"]
    empty_by_modality = {row["modality"]: row for row in empty_payload["recommendations"]}
    assert empty_payload["sample_count"] == 0
    assert empty_payload["auto_apply"] is False
    assert empty_by_modality["body"]["action"] == "collect_more_samples"
    assert empty_by_modality["body"]["recommended_threshold"] == empty_by_modality["body"]["current_threshold"]

    create_annotation(client, label="false_positive", track_id="track-a1", evidence_ref="job-001/frame-a1")
    create_annotation(client, label="mismatch", track_id="track-a2", evidence_ref="job-001/frame-a2")
    create_annotation(client, label="false_positive", track_id="track-a3", evidence_ref="job-001/frame-a3")
    create_annotation(client, label="low_quality", track_id="track-a4", evidence_ref="job-001/frame-a4")
    create_annotation(client, label="confirmed", track_id="track-a5", evidence_ref="job-001/frame-a5")
    create_annotation(client, tenant_id="tenant-b", label="confirmed", track_id="track-b1", evidence_ref="job-b/frame-1")
    create_annotation(client, tenant_id="tenant-b", label="confirmed", track_id="track-b2", evidence_ref="job-b/frame-2")
    create_annotation(client, tenant_id="tenant-b", label="confirmed", track_id="track-b3", evidence_ref="job-b/frame-3")

    response = client.get("/v1/evaluation/threshold-recommendations", headers=tenant_headers())
    other_tenant = client.get("/v1/evaluation/threshold-recommendations", headers=tenant_headers("tenant-b"))

    assert response.status_code == 200
    payload = response.json()["data"]
    recommendations = payload["threshold_recommendations"]
    by_modality = {row["modality"]: row for row in recommendations["recommendations"]}
    assert payload["tenant_id"] == "tenant-a"
    assert recommendations["method"] == "review_annotation_heuristic"
    assert recommendations["sample_count"] == 5
    assert recommendations["attention_count"] == 4
    assert recommendations["auto_apply"] is False
    assert all(row["auto_apply"] is False for row in recommendations["recommendations"])
    assert by_modality["body"]["action"] == "raise_threshold"
    assert by_modality["body"]["recommended_threshold"] > by_modality["body"]["current_threshold"]
    assert by_modality["fusion"]["recommended_threshold"] > by_modality["fusion"]["current_threshold"]
    assert by_modality["appearance"]["action"] == "review_quality_gate"
    assert by_modality["appearance"]["recommended_threshold"] == by_modality["appearance"]["current_threshold"]
    assert by_modality["body"]["evidence_counts"]["false_positive"] == 2
    assert by_modality["body"]["evidence_counts"]["confirmed"] == 1

    other_recommendations = other_tenant.json()["data"]["threshold_recommendations"]
    other_by_modality = {row["modality"]: row for row in other_recommendations["recommendations"]}
    assert other_recommendations["sample_count"] == 3
    assert other_by_modality["body"]["action"] == "hold_threshold"
    assert other_by_modality["body"]["recommended_threshold"] == other_by_modality["body"]["current_threshold"]
    assert "track-a" not in other_tenant.text

def test_track_review_summary_counts_labels_and_evidence_refs() -> None:
    client = TestClient(app)
    create_annotation(client, label="false_positive", track_id="track-7", evidence_ref="job-001/frame-12")
    create_annotation(client, label="confirmed", track_id="track-8", evidence_ref="job-001/frame-20")
    create_annotation(client, tenant_id="tenant-b", label="mismatch", track_id="track-b", evidence_ref="job-b/frame-1")

    response = client.get("/v1/evaluation/track-reviews/summary", headers=tenant_headers(), params={"limit": 5})
    filtered = client.get(
        "/v1/evaluation/track-reviews/summary",
        headers=tenant_headers(),
        params={"label": "confirmed"},
    )

    assert response.status_code == 200
    summary = response.json()["data"]["summary"]
    assert summary["count"] == 2
    assert summary["unique_job_count"] == 1
    assert summary["unique_track_count"] == 2
    assert {"label": "false_positive", "count": 1} in summary["label_counts"]
    assert {"label": "confirmed", "count": 1} in summary["label_counts"]
    assert summary["review_attention_count"] == 1
    assert len(summary["recent_annotations"]) == 2
    assert all(row["tenant_id"] == "tenant-a" for row in summary["recent_annotations"])
    assert {"job_id": "job-001", "count": 2} in summary["job_counts"]
    assert summary["evidence_index"][0]["evidence_ref"].startswith("job-001/")
    assert filtered.json()["data"]["summary"]["count"] == 1


def test_track_review_rejects_unknown_labels() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/evaluation/track-reviews",
        headers=tenant_headers(),
        json={"job_id": "job-001", "track_id": "track-7", "label": "delete_online_model"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported review label"


def test_track_review_annotation_rolls_back_when_audit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise HTTPException(status_code=503, detail="audit unavailable")

    monkeypatch.setattr(routes_portrait_review, "audit_event", fail_audit)

    response = client.post(
        "/v1/evaluation/track-reviews",
        headers=tenant_headers(),
        json={"job_id": "job-001", "track_id": "track-7", "label": "low_quality"},
    )
    rows = client.get("/v1/evaluation/track-reviews", headers=tenant_headers()).json()["data"]["annotations"]

    assert response.status_code == 503
    assert rows == []