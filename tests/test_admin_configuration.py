from __future__ import annotations

import os

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import admin_configuration, network_access_policy, routes_admin_configuration
from app.config_overrides import apply_configuration_overrides, read_configuration_override_values
from app.media import stream_decode
from app.portrait_access import validate_webhook_url
from main import app
from tools.apply_admin_configuration import merge_env_text, read_compose_overrides


def _client(monkeypatch, workspace_tmp_path) -> TestClient:
    monkeypatch.setattr(
        admin_configuration,
        "ADMIN_CONFIG_STATE_PATH",
        workspace_tmp_path / "admin-configuration.json",
    )
    monkeypatch.setattr(
        network_access_policy,
        "NETWORK_ACCESS_POLICY_PATH",
        workspace_tmp_path / "network-access-policy.json",
    )
    monkeypatch.setattr(routes_admin_configuration, "audit_event", lambda *args, **kwargs: None)
    return TestClient(app)


def test_configuration_catalog_covers_template_and_redacts_sensitive_values(monkeypatch, workspace_tmp_path) -> None:
    client = _client(monkeypatch, workspace_tmp_path)

    response = client.get("/v1/admin/configuration")

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["summary"]["total"] >= 200
    items = {item["key"]: item for item in payload["items"]}
    assert items["API_TOKEN"]["sensitive"] is True
    assert items["API_TOKEN"]["value"] is None
    assert items["STREAM_ALLOWED_CIDRS"]["managed_by"] == "network_policy"
    assert items["GPU_WORKER_0_DEVICE"]["apply_mode"] == "compose_recreate"


def test_configuration_update_stages_restart_value_without_returning_secret(monkeypatch, workspace_tmp_path) -> None:
    client = _client(monkeypatch, workspace_tmp_path)

    response = client.put(
        "/v1/admin/configuration",
        json={
            "expected_revision": 0,
            "changes": [
                {"key": "LOG_LEVEL", "value": "DEBUG"},
                {"key": "API_TOKEN", "value": "replace-this-token"},
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["revision"] == 1
    assert payload["changed_keys"] == ["API_TOKEN", "LOG_LEVEL"]
    items = {item["key"]: item for item in payload["items"]}
    assert items["LOG_LEVEL"]["desired_value"] == "DEBUG"
    assert items["LOG_LEVEL"]["pending"] is True
    assert items["API_TOKEN"]["desired_value"] is None
    assert items["API_TOKEN"]["override_configured"] is True
    assert "replace-this-token" not in response.text


def test_configuration_overrides_load_on_service_start(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "admin-configuration.json"
    state_path.write_text(
        '{"version":1,"revision":1,"values":{"LOG_LEVEL":"WARNING","MAX_TOP_K":"77"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("MAX_TOP_K", "100")

    values = apply_configuration_overrides(state_path)

    assert values == {"LOG_LEVEL": "WARNING", "MAX_TOP_K": "77"}
    assert read_configuration_override_values(state_path) == values
    assert os.environ["LOG_LEVEL"] == "WARNING"
    assert os.environ["MAX_TOP_K"] == "77"


def test_configuration_override_loader_fails_closed_on_corrupt_state(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "admin-configuration.json"
    state_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("STATE_READ_FAIL_CLOSED", "true")

    try:
        read_configuration_override_values(state_path)
    except RuntimeError as exc:
        assert "读取失败" in str(exc)
    else:
        raise AssertionError("corrupt administrator configuration must fail closed")


def test_removed_loaded_override_stays_pending_until_restart(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(admin_configuration, "ADMIN_CONFIG_STATE_PATH", workspace_tmp_path / "missing-state.json")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(
        admin_configuration.config_overrides,
        "APPLIED_CONFIGURATION_OVERRIDES",
        {"LOG_LEVEL": "DEBUG"},
    )
    monkeypatch.setattr(
        admin_configuration.config_overrides,
        "BASE_CONFIGURATION_ENVIRONMENT",
        {"LOG_LEVEL": "INFO"},
    )

    payload = admin_configuration.configuration_catalog_snapshot()
    item = next(entry for entry in payload["items"] if entry["key"] == "LOG_LEVEL")

    assert item["source"] == "override"
    assert item["overridden"] is False
    assert item["pending"] is True
    assert item["desired_value"] == "INFO"


def test_network_access_policy_allows_only_configured_private_cidrs(monkeypatch, workspace_tmp_path) -> None:
    client = _client(monkeypatch, workspace_tmp_path)

    response = client.put(
        "/v1/admin/network-access-policy",
        json={
            "expected_revision": 0,
            "stream": {
                "allow_private_hosts": True,
                "allowed_hosts": [],
                "allowed_cidrs": ["10.30.0.0/16"],
            },
            "webhook": {
                "allow_private_hosts": True,
                "allowed_hosts": [],
                "allowed_cidrs": ["10.40.0.0/24"],
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["stream"]["allowed_cidrs"] == ["10.30.0.0/16"]
    assert stream_decode.validate_media_stream_url("rtsp://10.30.2.8/live") == "rtsp://10.30.2.8/live"
    assert validate_webhook_url("http://10.40.0.9/events", required=True) == "http://10.40.0.9/events"

    try:
        stream_decode.validate_media_stream_url("rtsp://10.31.2.8/live")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "STREAM_ALLOWED_CIDRS" in str(exc.detail)
    else:
        raise AssertionError("unlisted stream CIDR should be rejected")

    try:
        validate_webhook_url("http://10.41.0.9/events", required=True)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "WEBHOOK_ALLOWED_CIDRS" in str(exc.detail)
    else:
        raise AssertionError("unlisted webhook CIDR should be rejected")


def test_network_access_policy_rejects_invalid_cidr(monkeypatch, workspace_tmp_path) -> None:
    client = _client(monkeypatch, workspace_tmp_path)

    response = client.put(
        "/v1/admin/network-access-policy",
        json={
            "expected_revision": 0,
            "stream": {"allow_private_hosts": True, "allowed_hosts": [], "allowed_cidrs": ["10.30.0.0/99"]},
            "webhook": {"allow_private_hosts": False, "allowed_hosts": [], "allowed_cidrs": []},
        },
    )

    assert response.status_code == 422
    assert not (workspace_tmp_path / "network-access-policy.json").exists()


def test_network_access_policy_rejects_unrestricted_private_access(monkeypatch, workspace_tmp_path) -> None:
    client = _client(monkeypatch, workspace_tmp_path)

    response = client.put(
        "/v1/admin/network-access-policy",
        json={
            "expected_revision": 0,
            "stream": {"allow_private_hosts": True, "allowed_hosts": [], "allowed_cidrs": []},
            "webhook": {"allow_private_hosts": False, "allowed_hosts": [], "allowed_cidrs": []},
        },
    )

    assert response.status_code == 422
    assert "至少一个主机或 CIDR" in response.text


def test_network_policy_rechecks_latest_revision_inside_write_lock(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(
        network_access_policy,
        "NETWORK_ACCESS_POLICY_PATH",
        workspace_tmp_path / "network-access-policy.json",
    )
    original = network_access_policy.default_network_access_policy(
        stream={"allow_private_hosts": False, "allowed_hosts": [], "allowed_cidrs": []},
        webhook={"allow_private_hosts": False, "allowed_hosts": [], "allowed_cidrs": []},
    )
    network_access_policy.save_network_access_policy(
        current=original,
        stream={"allow_private_hosts": True, "allowed_hosts": [], "allowed_cidrs": ["10.30.0.0/16"]},
        webhook=original["webhook"],
        updated_at=1.0,
        expected_revision=0,
    )

    try:
        network_access_policy.save_network_access_policy(
            current=original,
            stream={"allow_private_hosts": True, "allowed_hosts": [], "allowed_cidrs": ["10.40.0.0/16"]},
            webhook=original["webhook"],
            updated_at=2.0,
            expected_revision=0,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("stale network policy revision should be rejected inside the write lock")


def test_ip_host_rule_does_not_match_domain_suffix() -> None:
    assert network_access_policy.host_matches_rules("10.30.0.8", ["10.30.0.8"]) is True
    assert network_access_policy.host_matches_rules("camera.10.30.0.8", ["10.30.0.8"]) is False
    assert network_access_policy.host_matches_rules("camera.internal.example", ["internal.example"]) is True


def test_compose_configuration_export_updates_only_compose_keys(workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "admin-configuration.json"
    state_path.write_text(
        """{
          "version": 1,
          "revision": 3,
          "values": {
            "GPU_WORKER_0_DEVICE": "2",
            "STREAM_WORKER_FORCE_CPU": "false",
            "LOG_LEVEL": "DEBUG",
            "API_TOKEN": "must-not-export"
          }
        }""",
        encoding="utf-8",
    )

    values = read_compose_overrides(state_path)
    merged, changed = merge_env_text("GPU_WORKER_0_DEVICE=0\nLOG_LEVEL=INFO\n", values)

    assert values == {"GPU_WORKER_0_DEVICE": "2", "STREAM_WORKER_FORCE_CPU": "false"}
    assert changed == ["GPU_WORKER_0_DEVICE", "STREAM_WORKER_FORCE_CPU"]
    assert "GPU_WORKER_0_DEVICE=2" in merged
    assert "STREAM_WORKER_FORCE_CPU=false" in merged
    assert "LOG_LEVEL=INFO" in merged
    assert "must-not-export" not in merged
