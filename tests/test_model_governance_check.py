from argparse import Namespace
from hashlib import sha256
from pathlib import Path

import yaml

from tools.model_governance_check import validate_config


def test_model_governance_check_passes_for_repo_models() -> None:
    report = validate_config(
        Namespace(
            config="models.yml",
            models_root="models",
            capabilities="model-capabilities.yml",
            model_id=None,
            strict_hash=True,
            strict_sidecars=True,
            strict_governance=True,
            allow_missing_artifacts=True,
            json=True,
        )
    )

    assert report["ok"] is True
    assert report["model_count"] >= 2
    assert report["errors"] == []


def test_model_governance_check_rejects_missing_governance_sidecar(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "model_governance"
    models_root = root / "models"
    models_root.mkdir(parents=True)
    model_path = models_root / "sample.onnx"
    model_bytes = b"fake onnx bytes"
    model_path.write_bytes(model_bytes)
    digest = sha256(model_bytes).hexdigest()
    (models_root / "sample.model-card.yml").write_text(
        yaml.safe_dump(
            {
                "model": {"version": "1.0.0", "name": "sample"},
                "evaluation": {"accuracy": 0.99},
            }
        ),
        encoding="utf-8",
    )
    (root / "models.yml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "sample.onnx": {
                        "task": "reid",
                        "artifact": {
                            "path": "sample.onnx",
                            "model_card": "sample.model-card.yml",
                            "sha256": digest,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "model-capabilities.yml").write_text(
        yaml.safe_dump(
            {"capabilities": {"sample": {"status": "production", "model_id": "sample.onnx"}}}
        ),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(root / "models.yml"),
            models_root=str(models_root),
            capabilities=str(root / "model-capabilities.yml"),
            model_id=None,
            strict_hash=True,
            strict_sidecars=True,
            strict_governance=True,
            json=True,
        )
    )

    assert report["ok"] is False
    assert any("治理 sidecar 不存在" in error for error in report["errors"])


def test_model_governance_check_allows_missing_artifact_with_complete_metadata(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "metadata_only_model_governance"
    models_root = root / "models"
    models_root.mkdir(parents=True)
    expected_digest = "0" * 64
    governance_payload = {
        "dataset_lineage": {"source": "synthetic governance fixture"},
        "bias": {"assessment": "reviewed"},
        "threshold_calibration": {"method": "fixed fixture"},
        "risk_management": {"controls": ["human review"]},
        "human_review": {"required": True},
        "drift_monitoring": {"metric": "embedding distribution"},
        "privacy": {"pii": "not stored in fixture"},
        "release": {"approval": "test"},
    }
    (models_root / "sample.model-card.yml").write_text(
        yaml.safe_dump(
            {
                "model": {"version": "1.0.0", "name": "sample"},
                "evaluation": {"accuracy": 0.99},
            }
        ),
        encoding="utf-8",
    )
    (models_root / "sample.governance.yml").write_text(yaml.safe_dump(governance_payload), encoding="utf-8")
    (root / "models.yml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "sample.onnx": {
                        "task": "reid",
                        "artifact": {
                            "path": "sample.onnx",
                            "model_card": "sample.model-card.yml",
                            "governance": "sample.governance.yml",
                            "sha256": expected_digest,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "model-capabilities.yml").write_text(
        yaml.safe_dump({"capabilities": {"sample": {"status": "production", "model_id": "sample.onnx"}}}),
        encoding="utf-8",
    )

    report = validate_config(
        Namespace(
            config=str(root / "models.yml"),
            models_root=str(models_root),
            capabilities=str(root / "model-capabilities.yml"),
            model_id=None,
            strict_hash=True,
            strict_sidecars=True,
            strict_governance=True,
            allow_missing_artifacts=True,
            json=True,
        )
    )

    assert report["ok"] is True
    assert report["errors"] == []
    assert any("仅校验元数据" in warning for warning in report["warnings"])
