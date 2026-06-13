from argparse import Namespace
from hashlib import sha256
from pathlib import Path

import yaml

from tools.validate_model_package import validate_config


def test_validate_model_package_accepts_complete_package(workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "model_package_case"
    models_root = case_root / "models"
    model_dir = models_root / "project"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "classifier.onnx"
    model_bytes = b"fake onnx bytes"
    model_path.write_bytes(model_bytes)
    digest = sha256(model_bytes).hexdigest()
    (model_dir / "classifier.labels.txt").write_text("ok\nng\n", encoding="utf-8")
    (model_dir / "classifier.model-card.yml").write_text(
        yaml.safe_dump(
            {
                "model": {"version": "1.0.0", "precision": "fp32"},
                "evaluation": {"accuracy": 0.99},
            }
        ),
        encoding="utf-8",
    )
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {"classifier_default": {"target": "project/classifier.onnx"}},
                "models": {
                    "project/classifier.onnx": {
                        "task": "classification",
                        "runtime": "onnxruntime",
                        "input": {"size": [224, 224]},
                        "output": {"format": "classification"},
                        "artifact": {
                            "model_card": "classifier.model-card.yml",
                            "labels": "classifier.labels.txt",
                            "sha256": digest,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(config_path),
            models_root=str(models_root),
            model_id=None,
            strict_hash=True,
            strict_sidecars=True,
            json=True,
        )
    )

    assert report["ok"] is True
    assert report["model_count"] == 1
    assert report["errors"] == []


def test_validate_model_package_uses_artifact_path(workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "artifact_path_case"
    models_root = case_root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    model_path = models_root / "classifier.onnx"
    model_path.write_bytes(b"fake onnx bytes")
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "portrait_hub/classifier.onnx": {
                        "task": "classification",
                        "runtime": "onnxruntime",
                        "input": {"size": [224, 224]},
                        "output": {"classes": ["ok", "ng"]},
                        "artifact": {"path": "classifier.onnx"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(config_path),
            models_root=str(models_root),
            model_id=None,
            strict_hash=False,
            strict_sidecars=False,
            json=True,
        )
    )

    assert report["ok"] is True
    assert report["models"][0]["path"] == str(model_path.resolve())


def test_validate_model_package_rejects_path_like_model_keys(workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "bad_keys_case"
    models_root = case_root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "project/nested/model.onnx": {"task": "classification"},
                    "../project/model.onnx": {"task": "classification"},
                    " project/model.onnx": {"task": "classification"},
                    "project/model.onnx ": {"task": "classification"},
                },
            }
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(config_path),
            models_root=str(models_root),
            model_id=None,
            strict_hash=False,
            strict_sidecars=False,
            json=True,
        )
    )

    assert report["ok"] is False
    assert len(report["errors"]) == 4
    assert all("must not contain path separators" in error for error in report["errors"])


def test_validate_model_package_rejects_path_like_alias_targets(workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "bad_alias_case"
    models_root = case_root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {
                    "bad_static_padding": " project/model.onnx",
                    "bad_static": "project/nested/model.onnx",
                    "bad_mapping_padding": {"target": "project/model.onnx "},
                    "bad_mapping": {"target": "../project/model.onnx"},
                    "bad_project_padding": {"project_name": " project", "model_name": "model.onnx"},
                    "bad_project_model": {"project_name": "project", "model_name": "nested/model.onnx"},
                    "bad_rollout_padding": {"rollout": [{"target": "project/model.onnx ", "weight": 100}]},
                    "bad_rollout": {"rollout": [{"target": "project/nested/model.onnx", "weight": 100}]},
                },
                "models": {},
            }
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(config_path),
            models_root=str(models_root),
            model_id=None,
            strict_hash=False,
            strict_sidecars=False,
            json=True,
        )
    )

    assert report["ok"] is False
    assert sum("must not contain path separators" in error for error in report["errors"]) == 8


def test_validate_model_package_reports_bad_alias_rollout_weight(workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "bad_alias_weight_case"
    models_root = case_root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {
                    "bad_weight": {
                        "rollout": [
                            {"target": "project/model.onnx", "weight": "heavy"},
                            {"target": "project/other.onnx", "weight": -1},
                        ]
                    }
                },
                "models": {
                    "project/model.onnx": {"task": "classification"},
                    "project/other.onnx": {"task": "classification"},
                },
            }
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(config_path),
            models_root=str(models_root),
            model_id=None,
            strict_hash=False,
            strict_sidecars=False,
            json=True,
        )
    )

    assert report["ok"] is False
    assert "alias rollout weight must be an integer: bad_weight" in report["errors"]
    assert "alias rollout weight must be >= 0: bad_weight" in report["errors"]


def test_validate_model_package_rejects_alias_targets_missing_from_models(workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "missing_alias_target_case"
    models_root = case_root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {"classifier_default": {"target": "project/missing.onnx"}},
                "models": {"project/configured.onnx": {"task": "classification"}},
            }
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(config_path),
            models_root=str(models_root),
            model_id=None,
            strict_hash=False,
            strict_sidecars=False,
            json=True,
        )
    )

    assert report["ok"] is False
    assert "alias target is not in models mapping: classifier_default -> project/missing.onnx" in report["errors"]


def test_validate_model_package_checks_all_alias_rollout_candidates(workspace_tmp_path: Path) -> None:
    case_root = workspace_tmp_path / "missing_rollout_candidate_case"
    models_root = case_root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    config_path = case_root / "models.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "aliases": {
                    "weighted_default": {
                        "rollout": {
                            "targets": [
                                {"target": "project/configured.onnx", "weight": 99},
                                {"target": "project/missing-low-weight.onnx", "weight": 1},
                            ]
                        }
                    }
                },
                "models": {"project/configured.onnx": {"task": "classification"}},
            }
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(config_path),
            models_root=str(models_root),
            model_id=None,
            strict_hash=False,
            strict_sidecars=False,
            json=True,
        )
    )

    assert report["ok"] is False
    assert "alias target is not in models mapping: weighted_default -> project/missing-low-weight.onnx" in report["errors"]
