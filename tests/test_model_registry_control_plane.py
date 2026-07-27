import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app import (
    model_config_loader,
    model_config_writer,
    portrait_audit,
    portrait_model_registry,
    rollout_audit,
)
from app.server import app


@pytest.fixture
def registry_artifact(workspace_tmp_path: Path) -> Path:
    artifact_path = workspace_tmp_path / "osnet-test.onnx"
    artifact_path.write_bytes(b"isolated model registry test artifact")
    return artifact_path


@pytest.fixture
def registry_client(
    workspace_tmp_path: Path,
    registry_artifact: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    registry_path = workspace_tmp_path / "model-registry.json"
    config_path = workspace_tmp_path / "models.yml"
    config_path.write_text(Path("models.yml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(portrait_model_registry, "PORTRAIT_MODEL_REGISTRY_STATE_PATH", registry_path)
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(rollout_audit, "ROLLOUT_AUDIT_PATH", workspace_tmp_path / "rollout-audit.jsonl")
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", workspace_tmp_path / "audit.jsonl")
    monkeypatch.setattr(portrait_model_registry, "get_model_path", lambda *_: registry_artifact)
    portrait_model_registry.reset_model_registry_state()
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Tenant-ID": "tenant-a", "X-Project-ID": "default"}


def response_data(response: Response) -> dict[str, object]:
    status_code = response.status_code
    assert status_code < 400, response.text
    payload = response.json()
    return payload["data"]


def osnet_registration_payload(
    artifact_path: Path,
    *,
    sha256: str | None = None,
    version: str = "1.0.0",
) -> dict[str, object]:
    return {
        "name": "osnet-person-reid",
        "capability": "body_embedding",
        "version": version,
        "framework": "onnx",
        "runtime": "onnxruntime",
        "model_target": "portrait_hub/osnet_ibn_x1_0.onnx",
        "sha256": sha256 or hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "artifact_size": artifact_path.stat().st_size,
        "artifact_uri": str(artifact_path),
        "license": "verified-commercial-test-license",
        "source": "controlled-model-artifact",
        "redistribution_allowed": True,
        "model_card_ref": "models/osnet_ibn_x1_0.model-card.yml",
        "governance_ref": "models/osnet_ibn_x1_0.governance.yml",
        "quality_gates": {"mAP": {"min": 0.7}, "p95_latency_ms": {"max": 2000}},
        "supports_batching": True,
        "max_batch_size": 32,
    }


def register_and_evaluate(registry_client: TestClient, registry_artifact: Path) -> dict[str, object]:
    version = response_data(
        registry_client.post(
            "/v1/admin/models/registry",
            headers=headers(),
            json=osnet_registration_payload(registry_artifact),
        )
    )["model_version"]
    evaluation = response_data(
        registry_client.post(
            f"/v1/admin/models/registry/versions/{version['model_version_id']}/evaluations",
            headers=headers(),
            json={
                "dataset_id": "held-out-reid-v1",
                "dataset_manifest_sha256": "a" * 64,
                "metrics": {"mAP": 0.81, "p95_latency_ms": 120},
                "report_ref": "evidence/reid-evaluation.json",
            },
        )
    )["evaluation"]
    assert evaluation["passed"] is True
    return version


def test_registry_requires_provenance_and_quality_gate(
    registry_client: TestClient,
    registry_artifact: Path,
) -> None:
    invalid = osnet_registration_payload(registry_artifact)
    invalid["license"] = ""
    rejected = registry_client.post("/v1/admin/models/registry", headers=headers(), json=invalid)
    assert rejected.status_code == 422

    version = response_data(
        registry_client.post(
            "/v1/admin/models/registry",
            headers=headers(),
            json=osnet_registration_payload(registry_artifact),
        )
    )["model_version"]
    failed = response_data(
        registry_client.post(
            f"/v1/admin/models/registry/versions/{version['model_version_id']}/evaluations",
            headers=headers(),
            json={
                "dataset_id": "held-out-reid-v1",
                "dataset_manifest_sha256": "b" * 64,
                "metrics": {"mAP": 0.65, "p95_latency_ms": 100},
            },
        )
    )["evaluation"]
    assert failed["passed"] is False
    versions = response_data(
        registry_client.get(
            f"/v1/admin/models/registry/{version['model_id']}/versions",
            headers=headers(),
        )
    )["versions"]
    assert versions[0]["status"] == "draft"


def test_release_preflight_checks_digest_evaluation_and_separation_of_duties(
    registry_client: TestClient,
    registry_artifact: Path,
) -> None:
    version = register_and_evaluate(registry_client, registry_artifact)
    release = {
        "model_version_id": version["model_version_id"],
        "alias": "person_reid_default",
        "action": "canary",
        "risk_level": "low",
        "traffic_percentage": 10,
        "reason": "held-out and latency gates passed",
    }
    blocked = response_data(
        registry_client.post(
            "/v1/admin/models/releases/dry-run",
            headers=headers(),
            json=release,
        )
    )["release_preflight"]
    assert blocked["ok"] is False
    assert "release approval policy is not satisfied" in blocked["blockers"]

    portrait_model_registry.create_model_approval(
        str(version["model_version_id"]),
        {"decision": "approve", "policy": "model_release", "comment": "independent approval"},
        actor="release-approver",
        request_id="approval-request",
    )
    ready = response_data(
        registry_client.post(
            "/v1/admin/models/releases/dry-run",
            headers=headers(),
            json=release,
        )
    )["release_preflight"]
    assert ready["ok"] is True
    assert ready["artifact"]["sha256_matches"] is True

    applied = response_data(
        registry_client.post(
            "/v1/admin/models/releases/apply",
            headers=headers(),
            json=release,
        )
    )
    assert applied["release"]["outcome"] == "success"
    assert applied["release"]["action"] == "canary"
    promoted = response_data(
        registry_client.post(
            "/v1/admin/models/releases/apply",
            headers=headers(),
            json={**release, "action": "activate"},
        )
    )
    assert promoted["release"]["action"] == "activate"
    events = response_data(
        registry_client.get("/v1/admin/models/releases/audit", headers=headers())
    )["release_events"]
    assert events[0]["release_event_id"] == promoted["release"]["release_event_id"]
    assert events[1]["release_event_id"] == applied["release"]["release_event_id"]


def test_release_preflight_rejects_artifact_digest_mismatch(
    registry_client: TestClient,
    registry_artifact: Path,
) -> None:
    version = response_data(
        registry_client.post(
            "/v1/admin/models/registry",
            headers=headers(),
            json=osnet_registration_payload(registry_artifact, sha256="0" * 64),
        )
    )["model_version"]
    portrait_model_registry.create_model_evaluation(
        str(version["model_version_id"]),
        {
            "dataset_id": "held-out-reid-v1",
            "dataset_manifest_sha256": "c" * 64,
            "metrics": {"mAP": 0.9, "p95_latency_ms": 100},
        },
        actor="evaluator",
        request_id="evaluation-request",
    )
    portrait_model_registry.create_model_approval(
        str(version["model_version_id"]),
        {"decision": "approve"},
        actor="release-approver",
        request_id="approval-request",
    )
    preflight = response_data(
        registry_client.post(
            "/v1/admin/models/releases/dry-run",
            headers=headers(),
            json={
                "model_version_id": version["model_version_id"],
                "alias": "person_reid_default",
                "action": "activate",
                "risk_level": "low",
                "reason": "test mismatch",
            },
        )
    )["release_preflight"]
    assert preflight["ok"] is False
    assert "model artifact digest does not match" in preflight["blockers"]


def test_registry_lists_logical_models_and_versions(
    registry_client: TestClient,
    registry_artifact: Path,
) -> None:
    version = register_and_evaluate(registry_client, registry_artifact)
    models = response_data(registry_client.get("/v1/admin/models/registry", headers=headers()))
    assert models["count"] == 1
    assert models["models"][0]["version_count"] == 1
    assert models["models"][0]["latest_version"]["model_version_id"] == version["model_version_id"]
