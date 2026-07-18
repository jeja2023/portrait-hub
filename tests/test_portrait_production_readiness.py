from pathlib import Path

import yaml

from tools.portrait_cutover_check import run_cutover_check, sha256_file
from tools.portrait_production_readiness import check_security_controls, check_templates
from tools.readiness_checks import check_capabilities, check_model_files, configured_model_path


def test_readiness_model_check_uses_artifact_path(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "readiness"
    models_root = root / "models"
    models_root.mkdir(parents=True)
    (models_root / "detector.onnx").write_bytes(b"fake onnx")
    (root / "models.yml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "portrait_hub/detector.onnx": {
                        "artifact": {"path": "detector.onnx"},
                        "task": "detection",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    checks = check_model_files(root, models_root)

    assert checks == [
        {
            "name": "model_file:portrait_hub/detector.onnx",
            "ok": True,
            "path": str((models_root / "detector.onnx").resolve()),
            "error": None,
        }
    ]


def test_readiness_model_check_rejects_escaping_artifact_path(workspace_tmp_path: Path) -> None:
    models_root = (workspace_tmp_path / "models").resolve()
    models_root.mkdir(parents=True)

    path, error = configured_model_path(
        "portrait_hub/detector.onnx",
        {"artifact": {"path": "../detector.onnx"}},
        models_root,
    )

    assert path is None
    assert error == "模型构件路径逃逸模型根目录"


def test_ci_security_audit_workflow_runs_pip_audit() -> None:
    workflow = Path(".github/workflows/security-audit.yml")

    assert workflow.exists()
    content = workflow.read_text(encoding="utf-8")
    assert "pip-audit" in content
    assert "python tools/security_audit.py" in content
    assert "cron:" in content


def test_readiness_templates_include_cutover_and_worker_artifacts() -> None:
    checks = {item["name"]: item for item in check_templates(Path("."))}

    for item in [
        "frontend/console-next/package.json",
        "package.json",
        "tools/portrait_cutover_check.py",
        "tools/portrait_model_regression.py",
        "tools/portrait_stream_worker_health.py",
        "examples/production-models.example.yml",
        "examples/production-model-capabilities.example.yml",
        "deploy/portrait-stream-worker.service",
        "deploy/k8s-stream-worker.yaml",
        ".github/workflows/ci.yml",
    ]:
        assert checks[f"template:{item}"]["ok"] is True


def test_readiness_tracks_console_next_cutover() -> None:
    checks = {item["name"]: item for item in check_security_controls(Path("."))}

    assert checks["frontend:legacy_console_removed"]["ok"] is True
    assert checks["frontend:console_next_production_chain"]["ok"] is True


def test_readiness_strict_console_and_governance_contracts_hold() -> None:
    checks = {item["name"]: item for item in check_security_controls(Path("."))}
    expected = [
        "sdk:batch_async_and_video_examples",
        "frontend:api_playground_stage_two_coverage",
        "frontend:slo_panel_operational_contract",
        "security:least_privilege_rbac_roles",
        "security:access_center_state_safety",
        "security:access_error_code_catalog",
        "security:track_review_annotation_pool",
        "security:upload_validation_error_minimal_disclosure",
        "security:audit_chain_console_verification",
        "security:audit_event_readback",
        "security:backup_snapshot_readback",
    ]

    assert {name: checks[name]["ok"] for name in expected} == dict.fromkeys(expected, True)

def test_runtime_error_redaction_contract_holds_in_repo() -> None:
    # 锁定运行期错误脱敏契约：响应只回 request-id、日志只记脱敏摘要。这两项由共享的
    # inference_error_boundary 统一实现，任何路由绕过边界、内联泄露 exc 或重新引入
    # logger.exception，都会让下面的断言转红，从而被 CI 门禁拦截。
    checks = {item["name"]: item for item in check_security_controls(Path("."))}

    assert checks["security:runtime_error_response_redaction"]["ok"] is True
    assert checks["security:runtime_error_log_minimal_disclosure"]["ok"] is True


def test_readiness_accepts_ready_or_production_capabilities(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "ready-capabilities"
    root.mkdir()
    (root / "models.yml").write_text(
        yaml.safe_dump(
            {
                "aliases": {
                    "gait_default": {"target": "portrait_hub/opengait.onnx"},
                },
                "models": {
                    "portrait_hub/arcface.onnx": {"task": "reid"},
                    "portrait_hub/opengait.onnx": {"task": "gait"},
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "model-capabilities.yml").write_text(
        yaml.safe_dump(
            {
                "capabilities": {
                    "face_embedding": {
                        "status": "production",
                        "model_id": "portrait_hub/arcface.onnx",
                        "fallback_model_id": "portrait_hub/image_fingerprint_v1",
                    },
                    "gait": {
                        "status": "ready",
                        "model_id": "gait_default",
                        "fallback_model_id": "portrait_hub/tracklet_fingerprint_v1",
                    },
                    "appearance": {
                        "status": "fallback",
                        "model_id": "portrait_hub/color_histogram_v1",
                        "fallback_model_id": "portrait_hub/color_histogram_v1",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    checks = {item["name"]: item for item in check_capabilities(root)}

    assert checks["capability:face_embedding"]["ok"] is True
    assert checks["capability:face_embedding"]["resolved_model_id"] == "portrait_hub/arcface.onnx"
    assert checks["capability:gait"]["ok"] is True
    assert checks["capability:gait"]["resolved_model_id"] == "portrait_hub/opengait.onnx"
    assert checks["capability:appearance"]["ok"] is False


def test_readiness_rejects_ready_capability_without_configured_model(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "missing-capability-model"
    root.mkdir()
    (root / "models.yml").write_text(yaml.safe_dump({"models": {}}), encoding="utf-8")
    (root / "model-capabilities.yml").write_text(
        yaml.safe_dump(
            {
                "capabilities": {
                    "face_embedding": {
                        "status": "production",
                        "model_id": "portrait_hub/missing_arcface.onnx",
                        "fallback_model_id": "portrait_hub/image_fingerprint_v1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    checks = {item["name"]: item for item in check_capabilities(root)}

    assert checks["capability:face_embedding"]["ok"] is False
    assert checks["capability:face_embedding"]["model_error"] == "model_id 未在 models.yml 或 aliases 中配置"


def test_cutover_check_passes_with_real_artifact_hashes(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "cutover"
    models_root = root / "models"
    models_root.mkdir(parents=True)
    artifacts = {
        "portrait_hub/scrfd_10g.onnx": "scrfd_10g.onnx",
        "portrait_hub/arcface_r100.onnx": "arcface_r100.onnx",
        "portrait_hub/rtmpose_coco17.onnx": "rtmpose_coco17.onnx",
        "portrait_hub/opengait_gait3d.onnx": "opengait_gait3d.onnx",
        "portrait_hub/attribute_reid.onnx": "attribute_reid.onnx",
    }
    for model_id, filename in artifacts.items():
        (models_root / filename).write_bytes(f"fake artifact for {model_id}".encode())
    models = {
        model_id: {
            "task": model_id.split("/")[-1].split("_", 1)[0],
            "artifact": {"path": filename, "sha256": sha256_file(models_root / filename)},
        }
        for model_id, filename in artifacts.items()
    }
    models_path = root / "models.yml"
    capabilities_path = root / "model-capabilities.yml"
    models_path.write_text(yaml.safe_dump({"models": models}), encoding="utf-8")
    capabilities_path.write_text(
        yaml.safe_dump(
            {
                "capabilities": {
                    "face_detection": {
                        "status": "production",
                        "model_id": "portrait_hub/scrfd_10g.onnx",
                        "adapter": "scrfd",
                        "fallback_model_id": "opencv/haarcascade_frontalface_default",
                    },
                    "face_embedding": {
                        "status": "production",
                        "model_id": "portrait_hub/arcface_r100.onnx",
                        "adapter": "arcface",
                        "embedding_dim": 512,
                        "fallback_model_id": "portrait_hub/image_fingerprint_v1",
                    },
                    "pose": {
                        "status": "production",
                        "model_id": "portrait_hub/rtmpose_coco17.onnx",
                        "adapter": "rtmpose",
                        "fallback_model_id": "portrait_hub/geometric_pose_placeholder",
                    },
                    "gait": {
                        "status": "production",
                        "model_id": "portrait_hub/opengait_gait3d.onnx",
                        "adapter": "opengait",
                        "embedding_dim": 256,
                        "fallback_model_id": "portrait_hub/tracklet_fingerprint_v1",
                    },
                    "appearance": {
                        "status": "production",
                        "model_id": "portrait_hub/attribute_reid.onnx",
                        "adapter": "attribute_reid",
                        "embedding_dim": 256,
                        "fallback_model_id": "portrait_hub/color_histogram_v1",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = run_cutover_check(
        root=root,
        models_root=models_root,
        models_config_path=models_path,
        capabilities_path=capabilities_path,
        regression_manifest=Path("examples/portrait-model-regression.example.yml").resolve(),
        validate_onnx=False,
    )

    assert report["ok"] is True


def test_cutover_check_fails_when_capability_still_uses_fallback(workspace_tmp_path: Path) -> None:
    root = workspace_tmp_path / "cutover-fallback"
    models_root = root / "models"
    models_root.mkdir(parents=True)
    models_path = root / "models.yml"
    capabilities_path = root / "model-capabilities.yml"
    models_path.write_text(yaml.safe_dump({"models": {}}), encoding="utf-8")
    capabilities_path.write_text(
        yaml.safe_dump(
            {
                "capabilities": {
                    "face_detection": {
                        "status": "fallback",
                        "model_id": "opencv/haarcascade_frontalface_default",
                        "adapter": "haar_face_detection",
                        "fallback_model_id": "opencv/haarcascade_frontalface_default",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = run_cutover_check(
        root=root,
        models_root=models_root,
        models_config_path=models_path,
        capabilities_path=capabilities_path,
        regression_manifest=Path("examples/portrait-model-regression.example.yml").resolve(),
        validate_onnx=False,
    )

    assert report["ok"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "capability:face_detection" in failed
