from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app import portrait_audit, portrait_feedback
from app.portrait_access import clear_access_state
from app.server import app


@pytest.fixture
def feedback_client(workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def sample_payload(source_item_id: str, reason: str = "low_confidence") -> dict[str, Any]:
    return {
        "source_request_id": "request-1",
        "source_type": "image_inference",
        "source_item_id": source_item_id,
        "reason": reason,
        "priority": 80,
        "risk_level": "high",
        "confidence": 0.49,
        "model_id": "person-reid",
        "model_version_id": "model-version-1",
        "model_sha256": "a" * 64,
        "object_ref": f"s3://private-bucket/{source_item_id}.jpg",
        "masked_preview_ref": f"preview://{source_item_id}",
        "content_sha256": ("b" if source_item_id == "item-1" else "c") * 64,
        "proposed_labels": {"person": True},
        "tags": ["long-tail"],
    }


def create_sample(client: TestClient, source_item_id: str) -> dict[str, Any]:
    return response_data(
        client.post(
            "/v1/evaluation/review-samples",
            headers=headers(),
            json=sample_payload(source_item_id),
        )
    )["review_sample"]


def test_review_samples_are_deduplicated_prioritized_and_redacted(feedback_client: TestClient) -> None:
    first = create_sample(feedback_client, "item-1")
    duplicate = create_sample(feedback_client, "item-1")
    assert duplicate["review_sample_id"] == first["review_sample_id"]
    assert "object_ref" not in first
    assert first["object_available"] is True
    assert first["selection_score"] > 1

    listed = response_data(feedback_client.get("/v1/evaluation/review-samples", headers=headers()))
    assert listed["count"] == 1
    assert listed["review_samples"][0]["masked_preview_ref"] == "preview://item-1"
    other_tenant = response_data(
        feedback_client.get("/v1/evaluation/review-samples", headers=headers("tenant-b"))
    )
    assert other_tenant["count"] == 0


def test_annotation_export_import_conflicts_and_dataset_manifest(feedback_client: TestClient) -> None:
    first = create_sample(feedback_client, "item-1")
    second = create_sample(feedback_client, "item-2")
    exported = response_data(
        feedback_client.post(
            "/v1/evaluation/review-samples/export",
            headers=headers(),
            json={
                "sample_ids": [first["review_sample_id"], second["review_sample_id"]],
                "format": "label_studio",
                "schema_version": "labels-v1",
            },
        )
    )["annotation_export"]
    assert exported["sample_count"] == 2
    assert len(exported["sha256"]) == 64
    assert all("object_ref" not in task["data"] for task in exported["tasks"])

    imported = response_data(
        feedback_client.post(
            "/v1/evaluation/review-samples/import",
            headers=headers(),
            json={
                "annotation_export_id": exported["annotation_export_id"],
                "schema_version": "labels-v1",
                "annotations": [
                    {"review_sample_id": first["review_sample_id"], "labels": {"person": True, "quality": "good"}},
                    {"review_sample_id": second["review_sample_id"], "labels": {"person": False, "quality": "blurred"}},
                ],
            },
        )
    )["annotation_import"]
    assert imported["applied_count"] == 2

    conflict = feedback_client.post(
        "/v1/evaluation/review-samples/import",
        headers=headers(),
        json={
            "annotation_export_id": exported["annotation_export_id"],
            "annotations": [
                {"review_sample_id": first["review_sample_id"], "labels": {"person": False}},
            ],
        },
    )
    assert conflict.status_code == 409

    leakage = feedback_client.post(
        "/v1/evaluation/datasets",
        headers=headers(),
        json={
            "name": "reid-held-out",
            "version": "1.0.0",
            "splits": {
                "train": [first["review_sample_id"]],
                "test": [first["review_sample_id"], second["review_sample_id"]],
            },
        },
    )
    assert leakage.status_code == 409

    manifest = response_data(
        feedback_client.post(
            "/v1/evaluation/datasets",
            headers=headers(),
            json={
                "name": "reid-held-out",
                "version": "1.0.0",
                "definition_version": "dataset-manifest-v1",
                "label_schema_version": "labels-v1",
                "splits": {
                    "train": [first["review_sample_id"]],
                    "test": [second["review_sample_id"]],
                },
                "lineage": [exported["annotation_export_id"], imported["annotation_import_id"]],
            },
        )
    )["dataset_manifest"]
    assert manifest["immutable"] is True
    assert manifest["sample_count"] == 2
    assert len(manifest["sha256"]) == 64

    fetched = response_data(
        feedback_client.get(
            f"/v1/evaluation/datasets/{manifest['dataset_id']}/manifest",
            headers=headers(),
        )
    )["dataset_manifest"]
    assert fetched["sha256"] == manifest["sha256"]
    datasets = response_data(feedback_client.get("/v1/evaluation/datasets", headers=headers()))
    assert any(item.get("dataset_id") == manifest["dataset_id"] for item in datasets["datasets"])


def test_import_rejects_samples_outside_export(feedback_client: TestClient) -> None:
    first = create_sample(feedback_client, "item-1")
    second = create_sample(feedback_client, "item-2")
    exported = response_data(
        feedback_client.post(
            "/v1/evaluation/review-samples/export",
            headers=headers(),
            json={"sample_ids": [first["review_sample_id"]], "format": "cvat"},
        )
    )["annotation_export"]
    rejected = feedback_client.post(
        "/v1/evaluation/review-samples/import",
        headers=headers(),
        json={
            "annotation_export_id": exported["annotation_export_id"],
            "annotations": [
                {"review_sample_id": second["review_sample_id"], "labels": {"person": True}},
            ],
        },
    )
    assert rejected.status_code == 422
