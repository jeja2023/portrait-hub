from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from app import model_config_writer


def write_model_config(config_path: Path, payload: dict) -> None:
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_switch_and_rollback_alias_target(monkeypatch, workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "rollout_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {"detector_default": {"target": "project/old.onnx"}},
                "models": {
                    "project/old.onnx": {"task": "detection"},
                    "project/new.onnx": {"task": "detection"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_writer, "write_rollout_audit", lambda event, payload: None)

    switched = model_config_writer.switch_alias_target(
        "detector_default",
        "project/new.onnx",
        expected_current_target="project/old.onnx",
    )

    assert switched["old_target"] == "project/old.onnx"
    assert switched["new_target"] == "project/new.onnx"
    assert switched["config_loaded"] is True
    assert "config_path" not in switched
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["aliases"]["detector_default"]["target"] == "project/new.onnx"
    assert raw["aliases"]["detector_default"]["previous_target"] == "project/old.onnx"

    rolled_back = model_config_writer.rollback_alias_target("detector_default")

    assert rolled_back["old_target"] == "project/new.onnx"
    assert rolled_back["new_target"] == "project/old.onnx"
    assert rolled_back["config_loaded"] is True
    assert "config_path" not in rolled_back
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["aliases"]["detector_default"]["target"] == "project/old.onnx"


def test_configure_weighted_alias_rollout(monkeypatch, workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "weighted_rollout_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {"detector_default": {"target": "project/old.onnx"}},
                "models": {
                    "project/old.onnx": {"task": "detection"},
                    "project/new.onnx": {"task": "detection"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_writer, "write_rollout_audit", lambda event, payload: None)

    result = model_config_writer.configure_weighted_alias_rollout(
        "detector_default",
        [
            {"target_model_id": "project/old.onnx", "weight": 90, "status": "active"},
            {"target_model_id": "project/new.onnx", "weight": 10, "status": "candidate"},
        ],
        expected_current_target="project/old.onnx",
    )

    assert result["total_weight"] == 100
    assert result["config_loaded"] is True
    assert "config_path" not in result
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "target" not in raw["aliases"]["detector_default"]
    assert raw["aliases"]["detector_default"]["rollout"][1]["target"] == "project/new.onnx"


def test_alias_switch_rolls_back_config_when_audit_fails(monkeypatch, workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "audit_rollback_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    initial = {
        "aliases": {"detector_default": {"target": "project/old.onnx"}},
        "models": {
            "project/old.onnx": {"task": "detection"},
            "project/new.onnx": {"task": "detection"},
        },
    }
    config_path.write_text(yaml.safe_dump(initial, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)

    def fail_audit(event, payload):
        raise OSError("审计不可用 secret-token")

    monkeypatch.setattr(model_config_writer, "write_rollout_audit", fail_audit)

    with pytest.raises(HTTPException) as exc_info:
        model_config_writer.switch_alias_target("detector_default", "project/new.onnx")

    assert exc_info.value.status_code == 500
    assert "已回滚" in str(exc_info.value.detail)
    assert "secret-token" not in str(exc_info.value.detail)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw == initial


def test_model_config_writer_file_failure_logs_are_redacted(monkeypatch, workspace_tmp_path: Path, caplog) -> None:
    caplog.set_level("WARNING")
    secret_dir = workspace_tmp_path / "secret-config-dir"
    secret_dir.mkdir(parents=True, exist_ok=True)
    config_path = secret_dir / "secret-models.yml"
    config_path.write_text("models: {}\n", encoding="utf-8")
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)

    original_open = type(config_path).open

    def fail_open(self, *args, **kwargs):
        if self == config_path or secret_dir in self.parents:
            raise OSError(f"secret-token leaked through writer exception for {self}")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(config_path), "open", fail_open)

    with pytest.raises(HTTPException) as read_exc:
        model_config_writer.load_raw_model_config()
    with pytest.raises(HTTPException) as write_exc:
        model_config_writer.write_raw_model_config({"models": {}})

    assert read_exc.value.status_code == 500
    assert read_exc.value.detail == "读取模型配置文件失败"
    assert write_exc.value.status_code == 500
    assert write_exc.value.detail == "写入模型配置文件失败"
    assert "config_path_hash=" in caplog.text
    assert "OSError" in caplog.text
    for secret in ["secret-config-dir", "secret-models", "secret-token", str(config_path)]:
        assert secret not in caplog.text
        assert secret not in str(read_exc.value.detail)
        assert secret not in str(write_exc.value.detail)


def test_alias_switch_rollback_failure_is_redacted(monkeypatch, workspace_tmp_path: Path, caplog) -> None:
    caplog.set_level("WARNING")
    case_root = workspace_tmp_path / "audit_rollback_failure_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    initial = {
        "aliases": {"detector_default": {"target": "project/old.onnx"}},
        "models": {
            "project/old.onnx": {"task": "detection"},
            "project/new.onnx": {"task": "detection"},
        },
    }
    config_path.write_text(yaml.safe_dump(initial, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(
        model_config_writer,
        "write_rollout_audit",
        lambda event, payload: (_ for _ in ()).throw(OSError("审计 secret-token")),
    )

    write_count = 0
    original_write = model_config_writer.write_raw_model_config

    def fail_second_write(raw):
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("rollback secret-token")
        return original_write(raw)

    monkeypatch.setattr(model_config_writer, "write_raw_model_config", fail_second_write)

    with pytest.raises(HTTPException) as exc_info:
        model_config_writer.switch_alias_target("detector_default", "project/new.onnx")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "message": "写入发布审计失败，且模型配置回滚失败",
        "rolled_back": False,
        "rollback_failed": True,
    }
    assert "secret-token" not in str(exc_info.value.detail)
    assert "OSError" in caplog.text
    assert "secret-token" not in caplog.text


def test_weighted_rollout_writes_audit_after_config(monkeypatch, workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "audit_success_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {"detector_default": {"target": "project/old.onnx"}},
                "models": {
                    "project/old.onnx": {"task": "detection"},
                    "project/new.onnx": {"task": "detection"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    audits = []
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_writer, "write_rollout_audit", lambda event, payload: audits.append((event, payload)))

    model_config_writer.configure_weighted_alias_rollout(
        "detector_default",
        [
            {"target_model_id": "project/old.onnx", "weight": 80},
            {"target_model_id": "project/new.onnx", "weight": 20},
        ],
    )

    assert audits
    assert audits[0][0] == "alias_weighted_rollout"


def test_writer_rejects_invalid_alias_and_target_ids(monkeypatch, workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "invalid_writer_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    initial = {
        "aliases": {"detector_default": {"target": "project/old.onnx"}},
        "models": {
            "project/old.onnx": {"task": "detection"},
            "project/new.onnx": {"task": "detection"},
        },
    }
    write_model_config(config_path, initial)
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_writer, "write_rollout_audit", lambda event, payload: None)

    cases = [
        lambda: model_config_writer.switch_alias_target("bad/alias", "project/new.onnx"),
        lambda: model_config_writer.switch_alias_target("detector_default", " project/new.onnx"),
        lambda: model_config_writer.switch_alias_target(
            "detector_default",
            "project/new.onnx",
            expected_current_target="project/old.onnx ",
        ),
        lambda: model_config_writer.configure_weighted_alias_rollout(
            "detector_default",
            [{"target_model_id": "project/new.onnx ", "weight": 100}],
        ),
        lambda: model_config_writer.configure_weighted_alias_rollout(
            "detector_default",
            [{"target_model_id": "project/new.onnx", "weight": 100}],
            expected_current_target=" project/old.onnx",
        ),
        lambda: model_config_writer.configure_weighted_alias_rollout(
            "detector_default",
            [{"target_model_id": "project/new.onnx", "weight": "heavy"}],
        ),
        lambda: model_config_writer.configure_weighted_alias_rollout("detector_default", ["project/new.onnx"]),
    ]
    for call in cases:
        with pytest.raises(HTTPException) as exc_info:
            call()
        assert exc_info.value.status_code == 400

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw == initial


def test_rollback_rejects_invalid_previous_target(monkeypatch, workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "invalid_rollback_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    initial = {
        "aliases": {"detector_default": {"target": "project/new.onnx", "previous_target": "project/old.onnx "}},
        "models": {
            "project/old.onnx": {"task": "detection"},
            "project/new.onnx": {"task": "detection"},
        },
    }
    write_model_config(config_path, initial)
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_config_writer, "write_rollout_audit", lambda event, payload: None)

    with pytest.raises(HTTPException) as exc_info:
        model_config_writer.rollback_alias_target("detector_default")

    assert exc_info.value.status_code == 400
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw == initial


def test_model_config_writer_errors_do_not_echo_alias_or_target(monkeypatch, workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "redacted_writer_case"
    case_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    initial = {
        "aliases": {"secret_alias": {"target": "project/good.onnx"}},
        "models": {"project/good.onnx": {"task": "detection"}},
    }
    write_model_config(config_path, initial)
    monkeypatch.setattr(model_config_writer, "MODEL_CONFIG_PATH", config_path)

    with pytest.raises(HTTPException) as missing_target:
        model_config_writer.switch_alias_target("secret_alias", "project/secret-target.onnx")
    with pytest.raises(HTTPException) as conflict:
        model_config_writer.switch_alias_target(
            "secret_alias",
            "project/good.onnx",
            expected_current_target="project/secret-expected.onnx",
        )
    with pytest.raises(HTTPException) as missing_alias:
        model_config_writer.rollback_alias_target("secret_missing_alias")
    with pytest.raises(HTTPException) as missing_previous:
        model_config_writer.rollback_alias_target("secret_alias")

    details = [
        str(missing_target.value.detail),
        str(conflict.value.detail),
        str(missing_alias.value.detail),
        str(missing_previous.value.detail),
    ]
    for detail in details:
        assert "secret_alias" not in detail
        assert "secret-target" not in detail
        assert "secret-expected" not in detail
        assert "secret_missing_alias" not in detail
