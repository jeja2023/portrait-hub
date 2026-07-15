from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.portrait_algorithm_eval import load_manifest  # noqa: E402
from tools.portrait_model_regression import run_model_regression  # noqa: E402

REQUIRED_PRODUCTION_CAPABILITIES: dict[str, dict[str, Any]] = {
    "face_detection": {"adapter": "scrfd"},
    "face_embedding": {"adapter": "arcface", "embedding_dim": 512},
    "pose": {"adapter": "rtmpose"},
    "gait": {"adapter": "opengait", "embedding_dim": 256},
}


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configured_model_path(model_id: str, config: dict[str, Any], models_root: Path) -> tuple[Path | None, str | None]:
    raw_artifact = config.get("artifact")
    artifact = raw_artifact if isinstance(raw_artifact, dict) else {}
    artifact_path = artifact.get("path")
    if isinstance(artifact_path, str) and artifact_path.strip():
        candidate = Path(artifact_path.strip())
        if candidate.is_absolute():
            return None, "artifact.path 必须相对于模型根目录"
        path = (models_root / candidate).resolve()
    else:
        path = (models_root / model_id).resolve()
    try:
        path.relative_to(models_root.resolve())
    except ValueError:
        return None, "模型构件路径逃逸模型根目录"
    return path, None


def check_capability_contract(capabilities: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, expected_raw in REQUIRED_PRODUCTION_CAPABILITIES.items():
        expected: dict[str, Any] = dict(expected_raw)
        item = capabilities.get(name)
        ok = isinstance(item, dict)
        status = item.get("status") if isinstance(item, dict) else None
        adapter = item.get("adapter") if isinstance(item, dict) else None
        model_id = item.get("model_id") if isinstance(item, dict) else None
        fallback_model_id = item.get("fallback_model_id") if isinstance(item, dict) else None
        detail = {
            "status": status,
            "adapter": adapter,
            "model_id": model_id,
            "fallback_model_id": fallback_model_id,
        }
        ok = ok and status in {"ready", "production"}
        ok = ok and adapter == expected["adapter"]
        ok = ok and isinstance(model_id, str) and bool(model_id.strip()) and model_id != fallback_model_id
        if "embedding_dim" in expected:
            try:
                raw_embedding_dim = item.get("embedding_dim") if isinstance(item, dict) else None
                detail["embedding_dim"] = int(raw_embedding_dim) if raw_embedding_dim is not None else None
                ok = ok and detail["embedding_dim"] == expected["embedding_dim"]
            except (TypeError, ValueError):
                ok = False
        checks.append({"name": f"capability:{name}", "ok": bool(ok), "detail": detail})
    return checks


def check_artifacts(
    capabilities: dict[str, Any],
    models: dict[str, Any],
    models_root: Path,
    *,
    validate_onnx: bool = False,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    ort = None
    if validate_onnx:
        try:
            import onnxruntime as ort_module

            ort = ort_module
        except Exception as exc:
            checks.append({"name": "onnxruntime_import", "ok": False, "detail": {"error_type": type(exc).__name__}})
            return checks

    for capability_name in REQUIRED_PRODUCTION_CAPABILITIES:
        capability = capabilities.get(capability_name)
        model_id = capability.get("model_id") if isinstance(capability, dict) else None
        config = models.get(model_id) if isinstance(model_id, str) else None
        if not isinstance(model_id, str) or not isinstance(config, dict):
            checks.append({"name": f"artifact:{capability_name}", "ok": False, "detail": {"model_id": model_id, "error": "模型配置不存在"}})
            continue
        path, error = configured_model_path(model_id, config, models_root)
        exists = bool(path and path.is_file())
        raw_artifact = config.get("artifact")
        artifact = raw_artifact if isinstance(raw_artifact, dict) else {}
        expected_sha = str(artifact.get("sha256") or "").strip().lower()
        actual_sha = sha256_file(path) if path and path.is_file() else None
        hash_ok = bool(actual_sha and expected_sha and expected_sha == actual_sha)
        ok = exists and not error and hash_ok
        detail: dict[str, Any] = {
            "model_id": model_id,
            "path": str(path) if path else None,
            "exists": exists,
            "sha256_configured": bool(expected_sha),
            "sha256_match": hash_ok,
            "error": error,
        }
        if validate_onnx and ok and ort is not None:
            try:
                session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
                detail["inputs"] = [{"name": item.name, "shape": list(item.shape), "type": item.type} for item in session.get_inputs()]
                detail["outputs"] = [{"name": item.name, "shape": list(item.shape), "type": item.type} for item in session.get_outputs()]
            except Exception as exc:
                ok = False
                detail["onnx_error_type"] = type(exc).__name__
        checks.append({"name": f"artifact:{capability_name}", "ok": bool(ok), "detail": detail})
    return checks


def check_regression(manifest_path: Path | None) -> list[dict[str, Any]]:
    if manifest_path is None:
        return [{"name": "regression:manifest", "ok": False, "detail": {"error": "生产切换必须提供 manifest"}}]
    if not manifest_path.is_file():
        return [{"name": "regression:manifest", "ok": False, "detail": {"path": str(manifest_path), "error": "manifest 不存在"}}]
    report = run_model_regression(load_manifest(manifest_path))
    metrics = report.get("metrics")
    metric_sections = sorted(metrics.keys()) if isinstance(metrics, dict) else []
    return [
        {
            "name": "regression:gates",
            "ok": bool(report.get("ok")),
            "detail": {
                "metric_sections": metric_sections,
                "gate_failures": report.get("gate_failures", []),
            },
        }
    ]


def run_cutover_check(
    *,
    root: Path,
    models_root: Path,
    models_config_path: Path,
    capabilities_path: Path,
    regression_manifest: Path | None,
    validate_onnx: bool = False,
) -> dict[str, Any]:
    model_payload = load_yaml(models_config_path)
    raw_models = model_payload.get("models", model_payload)
    capability_payload = load_yaml(capabilities_path)
    raw_capabilities = capability_payload.get("capabilities", capability_payload)
    models = raw_models if isinstance(raw_models, dict) else {}
    capabilities = raw_capabilities if isinstance(raw_capabilities, dict) else {}
    checks = [
        {"name": "config:models_yml", "ok": models_config_path.is_file() and bool(models), "detail": {"path": str(models_config_path)}},
        {"name": "config:model_capabilities_yml", "ok": capabilities_path.is_file() and bool(capabilities), "detail": {"path": str(capabilities_path)}},
        *check_capability_contract(capabilities),
        *check_artifacts(capabilities, models, models_root, validate_onnx=validate_onnx),
        *check_regression(regression_manifest),
    ]
    failures = [item for item in checks if not item["ok"]]
    return {
        "ok": not failures,
        "root": str(root),
        "models_root": str(models_root),
        "failure_count": len(failures),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 PortraitHub 真实模型生产切换就绪状态。")
    parser.add_argument("--root", default=".", help="项目根目录。")
    parser.add_argument("--models-root", default="models", help="模型构件根目录。")
    parser.add_argument("--models-config", default="models.yml", help="models.yml 路径。")
    parser.add_argument("--capabilities", default="model-capabilities.yml", help="model-capabilities.yml 路径。")
    parser.add_argument("--regression-manifest", help="留出集回归清单 YAML/JSON。")
    parser.add_argument("--validate-onnx", action="store_true", help="尝试使用 CPUExecutionProvider 加载模型构件。")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    report = run_cutover_check(
        root=root,
        models_root=(root / args.models_root).resolve() if not Path(args.models_root).is_absolute() else Path(args.models_root).resolve(),
        models_config_path=(root / args.models_config).resolve() if not Path(args.models_config).is_absolute() else Path(args.models_config).resolve(),
        capabilities_path=(root / args.capabilities).resolve() if not Path(args.capabilities).is_absolute() else Path(args.capabilities).resolve(),
        regression_manifest=(root / args.regression_manifest).resolve() if args.regression_manifest and not Path(args.regression_manifest).is_absolute() else (Path(args.regression_manifest).resolve() if args.regression_manifest else None),
        validate_onnx=args.validate_onnx,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"人像发布切换检查：{'通过' if report['ok'] else '失败'}")
        for item in report["checks"]:
            print(f"{'通过' if item['ok'] else '失败'}: {item['name']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
