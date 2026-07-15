import pytest
import yaml
from fastapi import HTTPException

from app import model_config_loader
from app.model_config_loader import load_model_config_document, normalize_model_config
from app.model_config_resolver import alias_resolution, alias_target


def test_normalize_model_config_maps_legacy_yolo_fields() -> None:
    config = normalize_model_config(
        "project/model.onnx",
        {
            "type": "yolo",
            "input_size": [640, 640],
            "confidence": 0.4,
            "iou": 0.5,
            "classes": "coco",
        },
    )

    assert config["task"] == "detection"
    assert config["type"] == "yolo"
    assert config["input"]["size"] == [640, 640]
    assert config["output"]["confidence"] == 0.4
    assert config["output"]["iou"] == 0.5
    assert config["output"]["classes"] == "coco"


def test_alias_target_uses_highest_weight_rollout() -> None:
    target = alias_target(
        "detector_default",
        {
            "rollout": [
                {"target": "project/old.onnx", "weight": 10},
                {"target": "project/new.onnx", "weight": 90},
            ]
        },
    )

    assert target == "project/new.onnx"


def test_alias_target_uses_weighted_rollout_with_traffic_key() -> None:
    config = {
        "rollout": [
            {"target": "project/old.onnx", "weight": 50},
            {"target": "project/new.onnx", "weight": 50},
        ]
    }

    first = alias_resolution("detector_default", config, traffic_key="customer-001")
    second = alias_resolution("detector_default", config, traffic_key="customer-001")

    assert first["target"] == second["target"]
    assert first["strategy"] == "weighted"


def test_alias_target_accepts_traffic_split_mapping() -> None:
    config = {
        "traffic_split": {
            "project/model-a.onnx": 80,
            "project/model-b.onnx": 20,
        }
    }

    first = alias_resolution("detector_default", config, traffic_key="customer-001")
    second = alias_resolution("detector_default", config, traffic_key="customer-001")

    assert first == second
    assert first["strategy"] == "weighted"
    assert first["target"] in {"project/model-a.onnx", "project/model-b.onnx"}
    assert first["total_weight"] == 100


def test_alias_target_rejects_path_like_targets() -> None:
    for config in [
        " project/model.onnx",
        {"target": "project/model.onnx "},
        "project/nested/model.onnx",
        {"target": "../project/model.onnx"},
        {"project_name": "project", "model_name": "nested/model.onnx"},
        {"rollout": [{"target": "project/nested/model.onnx", "weight": 100}]},
        {"rollout": [{"target": "project/model.onnx ", "weight": 100}]},
    ]:
        try:
            alias_target("detector_default", config)
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError(f"expected HTTPException for {config!r}")


def test_alias_resolution_errors_do_not_echo_alias_name() -> None:
    secret_alias = "secret_alias"

    with pytest.raises(HTTPException) as invalid_exc:
        alias_resolution(secret_alias, 7)
    with pytest.raises(HTTPException) as missing_target_exc:
        alias_resolution(secret_alias, {})

    assert invalid_exc.value.detail == "别名配置无效"
    assert missing_target_exc.value.detail == "别名没有目标模型"
    assert secret_alias not in str(invalid_exc.value.detail)
    assert secret_alias not in str(missing_target_exc.value.detail)


def test_load_model_config_document_skips_invalid_runtime_keys(monkeypatch, workspace_tmp_path, caplog) -> None:
    caplog.set_level("WARNING")
    config_path = workspace_tmp_path / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {
                    "detector_default": {"target": "project/good.onnx"},
                    "missing_target": {"target": "project/missing.onnx"},
                    "bad_target": {"target": "project/bad/nested.onnx"},
                    "bad_weight": {"rollout": [{"target": "project/good.onnx", "weight": "heavy"}]},
                    "zero_weight": {"rollout": [{"target": "project/good.onnx", "weight": 0}]},
                    "missing_rollout_candidate": {
                        "rollout": [
                            {"target": "project/good.onnx", "weight": 99},
                            {"target": "project/missing-candidate.onnx", "weight": 1},
                        ]
                    },
                    "bad/alias": {"target": "project/good.onnx"},
                    42: {"target": "project/good.onnx"},
                },
                "models": {
                    "project/good.onnx": {"task": "detection"},
                    "project/bad/nested.onnx": {"task": "detection"},
                    " project/padded.onnx": {"task": "detection"},
                    "project/not-a-mapping.onnx": "detection",
                    7: {"task": "detection"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", config_path)

    models, aliases = load_model_config_document()

    assert sorted(models) == ["project/good.onnx"]
    assert sorted(aliases) == ["detector_default"]
    assert "key_hash=" in caplog.text
    assert "alias_hash=" in caplog.text
    assert "ValueError" in caplog.text
    for secret in [
        "missing_target",
        "bad_target",
        "bad_weight",
        "zero_weight",
        "missing_rollout_candidate",
        "bad/alias",
        "project/bad/nested.onnx",
        "project/missing.onnx",
        "project/not-a-mapping.onnx",
        "heavy",
    ]:
        assert secret not in caplog.text


def test_load_model_config_document_fails_closed_when_config_is_missing(monkeypatch, workspace_tmp_path) -> None:
    config_path = workspace_tmp_path / "missing-models.yml"
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_READ_FAIL_CLOSED", True)

    with pytest.raises(RuntimeError, match="模型配置文件不存在") as exc_info:
        load_model_config_document()

    assert str(config_path) not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_load_model_config_document_can_fall_back_for_dev_missing_config(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", workspace_tmp_path / "missing-models.yml")
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_READ_FAIL_CLOSED", False)

    assert load_model_config_document() == ({}, {})


def test_load_model_config_failure_logs_are_redacted(monkeypatch, workspace_tmp_path, caplog) -> None:
    caplog.set_level("WARNING")
    secret_dir = workspace_tmp_path / "secret-config-dir"
    config_path = secret_dir / "secret-models.yml"
    secret_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text("models: {}\n", encoding="utf-8")
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_READ_FAIL_CLOSED", False)

    original_open = type(config_path).open

    def fail_open(self, *args, **kwargs):
        if self == config_path:
            raise OSError(f"secret-token leaked through loader exception for {self}")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(config_path), "open", fail_open)

    assert load_model_config_document() == ({}, {})
    assert "config_path_hash=" in caplog.text
    assert "OSError" in caplog.text
    for secret in ["secret-config-dir", "secret-models", "secret-token", str(config_path)]:
        assert secret not in caplog.text


def test_load_model_config_document_fails_closed_for_malformed_config(monkeypatch, workspace_tmp_path) -> None:
    config_path = workspace_tmp_path / "models.yml"
    config_path.write_text("models: [\n", encoding="utf-8")
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_READ_FAIL_CLOSED", True)

    with pytest.raises(RuntimeError, match="读取模型配置文件失败"):
        load_model_config_document()


def test_load_model_config_document_fails_closed_for_non_mapping_root(monkeypatch, workspace_tmp_path) -> None:
    config_path = workspace_tmp_path / "models.yml"
    config_path.write_text("- project/model.onnx\n", encoding="utf-8")
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_loader, "MODEL_CONFIG_READ_FAIL_CLOSED", True)

    with pytest.raises(RuntimeError, match="模型配置文件根节点必须是映射"):
        load_model_config_document()
