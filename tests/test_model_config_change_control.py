from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import model_config_loader, model_config_writer, portrait_audit, routes_model_query
from app.model_config import reload_model_config_state
from app.server import app


@pytest.fixture
def config_client(workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    config_path = workspace_tmp_path / "models.yml"
    config_path.write_text(Path("models.yml").read_text(encoding="utf-8"), encoding="utf-8")
    history_dir = workspace_tmp_path / "config-history"
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_HISTORY_DIR", history_dir)
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", workspace_tmp_path / "audit.jsonl")
    reload_model_config_state()
    yield TestClient(app, raise_server_exceptions=False)
    reload_model_config_state()


def headers() -> dict[str, str]:
    return {"X-Tenant-ID": "tenant-a", "X-Project-ID": "default"}


def current_document() -> dict:
    return model_config_writer.load_raw_model_config()


def test_config_preview_apply_and_rollback_by_immutable_fingerprint(config_client: TestClient) -> None:
    candidate = deepcopy(current_document())
    candidate["models"]["portrait_hub/osnet_ibn_x1_0.onnx"]["batching"] = {
        "enabled": True,
        "max_batch_size": 8,
        "max_wait_ms": 5,
    }
    preview_response = config_client.post(
        "/v1/admin/models/config/preview",
        headers=headers(),
        json={"document": candidate},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()["data"]
    assert preview["current_fingerprint"] != preview["candidate_fingerprint"]
    assert any(change["path"].endswith(".batching") for change in preview["changes"])

    stale = config_client.post(
        "/v1/admin/models/config/apply",
        headers=headers(),
        json={
            "document": candidate,
            "expected_current_fingerprint": "0" * 64,
            "reason": "enable validated batching",
        },
    )
    assert stale.status_code == 409

    applied_response = config_client.post(
        "/v1/admin/models/config/apply",
        headers=headers(),
        json={
            "document": candidate,
            "expected_current_fingerprint": preview["current_fingerprint"],
            "reason": "enable validated batching",
        },
    )
    assert applied_response.status_code == 200, applied_response.text
    applied = applied_response.json()["data"]
    assert applied["applied_fingerprint"] == preview["candidate_fingerprint"]
    assert (model_config_writer.MODEL_CONFIG_HISTORY_DIR / f"{applied['previous_fingerprint']}.yml").is_file()

    rollback_response = config_client.post(
        "/v1/admin/models/config/rollback",
        headers=headers(),
        json={
            "target_fingerprint": applied["previous_fingerprint"],
            "expected_current_fingerprint": applied["applied_fingerprint"],
            "reason": "batching regression detected",
        },
    )
    assert rollback_response.status_code == 200, rollback_response.text
    rolled_back = rollback_response.json()["data"]
    assert rolled_back["applied_fingerprint"] == applied["previous_fingerprint"]
    assert "batching" not in current_document()["models"]["portrait_hub/osnet_ibn_x1_0.onnx"]


def test_config_preview_rejects_alias_to_missing_model(config_client: TestClient) -> None:
    candidate = deepcopy(current_document())
    candidate["aliases"]["person_reid_default"] = {"target": "portrait_hub/missing.onnx"}

    response = config_client.post(
        "/v1/admin/models/config/preview",
        headers=headers(),
        json={"document": candidate},
    )

    assert response.status_code == 400


def test_config_apply_restores_file_when_audit_fails(
    config_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = current_document()
    candidate = deepcopy(original)
    candidate["models"]["portrait_hub/osnet_ibn_x1_0.onnx"]["batching"] = {"enabled": True}
    fingerprint = model_config_writer.model_config_document_fingerprint(original)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(routes_model_query, "audit_event", fail_audit)
    response = config_client.post(
        "/v1/admin/models/config/apply",
        headers=headers(),
        json={
            "document": candidate,
            "expected_current_fingerprint": fingerprint,
            "reason": "test audit rollback",
        },
    )

    assert response.status_code == 500
    restored = yaml.safe_load(model_config_writer.MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
    assert restored == original
