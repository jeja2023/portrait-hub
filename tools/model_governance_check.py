"""Production model governance checks for PortraitHub."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


REQUIRED_GOVERNANCE_SECTIONS = [
    "dataset_lineage",
    "bias",
    "threshold_calibration",
    "risk_management",
    "human_review",
    "drift_monitoring",
    "privacy",
    "release",
]


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def section_has_content(payload: dict[str, Any], name: str) -> bool:
    value = payload.get(name)
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def resolve_alias_target(name: str, aliases: dict[str, Any]) -> str | None:
    alias = aliases.get(name)
    if isinstance(alias, str):
        return alias.strip() or None
    if not isinstance(alias, dict):
        return None
    target = alias.get("target")
    if isinstance(target, str) and target.strip():
        return target.strip()
    project = alias.get("project_name")
    model = alias.get("model_name")
    if isinstance(project, str) and isinstance(model, str) and project.strip() and model.strip():
        return f"{project.strip()}/{model.strip()}"
    rollout = alias.get("rollout")
    if isinstance(rollout, dict):
        targets = rollout.get("targets") or rollout.get("candidates")
        if isinstance(targets, list):
            for item in targets:
                if isinstance(item, dict) and isinstance(item.get("target"), str) and item["target"].strip():
                    return item["target"].strip()
    return None


def resolve_model_ids(model_ids: list[str], aliases: dict[str, Any]) -> list[str]:
    resolved: list[str] = []
    for item in model_ids:
        target = resolve_alias_target(item, aliases)
        resolved.append(target or item)
    return sorted(dict.fromkeys(resolved))


def configured_model_path(model_id: str, config: dict[str, Any], models_root: Path) -> tuple[Path | None, str | None]:
    artifact = config.get("artifact") if isinstance(config.get("artifact"), dict) else {}
    raw_path = artifact.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        candidate = Path(raw_path.strip())
        if candidate.is_absolute():
            return None, "artifact.path must be relative to models root"
        path = (models_root / candidate).resolve()
    else:
        path = (models_root / model_id.split("/", 1)[-1]).resolve()
    try:
        path.relative_to(models_root.resolve())
    except ValueError:
        return None, "model artifact path escapes models root"
    return path, None


def governance_sidecar_path(model_path: Path, artifact: dict[str, Any]) -> Path:
    raw_path = artifact.get("governance")
    if isinstance(raw_path, str) and raw_path.strip():
        return (model_path.parent / raw_path.strip()).resolve()
    return model_path.with_suffix(".governance.yml")


def model_card_path(model_path: Path, artifact: dict[str, Any]) -> Path:
    raw_path = artifact.get("model_card")
    if isinstance(raw_path, str) and raw_path.strip():
        return (model_path.parent / raw_path.strip()).resolve()
    return model_path.with_suffix(".model-card.yml")


def labels_path(model_path: Path, artifact: dict[str, Any]) -> Path:
    raw_path = artifact.get("labels")
    if isinstance(raw_path, str) and raw_path.strip():
        return (model_path.parent / raw_path.strip()).resolve()
    return model_path.with_suffix(".labels.txt")


def active_model_ids(models: dict[str, Any], aliases: dict[str, Any], capabilities: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for capability in capabilities.values():
        if not isinstance(capability, dict):
            continue
        status = str(capability.get("status") or "").lower()
        if status not in {"ready", "production"}:
            continue
        model_id = capability.get("model_id")
        if isinstance(model_id, str) and model_id.strip():
            ids.append(resolve_alias_target(model_id.strip(), aliases) or model_id.strip())
    if ids:
        return sorted(dict.fromkeys(ids))
    return sorted(str(key) for key in models.keys())

def validate_model(
    model_id: str,
    config: dict[str, Any],
    models_root: Path,
    *,
    strict_hash: bool,
    strict_sidecars: bool,
    strict_governance: bool,
    allow_missing_artifacts: bool,
) -> dict[str, Any]:
    artifact = config.get("artifact") if isinstance(config.get("artifact"), dict) else {}
    model_path, error = configured_model_path(model_id, config, models_root)
    detail: dict[str, Any] = {"model_id": model_id, "path": str(model_path) if model_path else None, "error": error}
    if model_path is None or error is not None:
        return {"name": f"model:{model_id}", "ok": False, "detail": detail}

    artifact_present = model_path.is_file()
    detail["artifact_present"] = artifact_present
    expected_sha = str(artifact.get("sha256") or "").strip().lower()
    if expected_sha:
        detail["expected_sha256"] = expected_sha
    elif strict_hash or strict_governance:
        return {"name": f"model:{model_id}", "ok": False, "detail": {**detail, "error": "artifact.sha256 is required"}}

    if artifact_present:
        actual_sha = sha256_file(model_path)
        detail["sha256"] = actual_sha
        if expected_sha and expected_sha != actual_sha:
            return {"name": f"model:{model_id}", "ok": False, "detail": {**detail, "error": "sha256 mismatch"}}
    elif not allow_missing_artifacts:
        return {"name": f"model:{model_id}", "ok": False, "detail": {**detail, "error": "model artifact missing"}}

    card_path = model_card_path(model_path, artifact)
    sidecar_path = governance_sidecar_path(model_path, artifact)
    label_file = labels_path(model_path, artifact)
    detail["model_card"] = str(card_path)
    detail["governance"] = str(sidecar_path)

    if not card_path.is_file():
        if strict_sidecars or strict_governance:
            return {"name": f"model:{model_id}", "ok": False, "detail": {**detail, "error": "model card missing"}}
    else:
        card = load_yaml(card_path)
        missing = []
        if not section_has_content(card, "model"):
            missing.append("model")
        if not section_has_content(card, "evaluation") and not section_has_content(card, "metrics"):
            missing.append("evaluation_or_metrics")
        if missing and (strict_sidecars or strict_governance):
            return {"name": f"model:{model_id}", "ok": False, "detail": {**detail, "error": f"model card missing sections: {', '.join(missing)}"}}

    if strict_governance:
        if not sidecar_path.is_file():
            return {"name": f"model:{model_id}", "ok": False, "detail": {**detail, "error": "governance sidecar missing"}}
        governance = load_yaml(sidecar_path)
        missing_sections = [item for item in REQUIRED_GOVERNANCE_SECTIONS if not section_has_content(governance, item)]
        detail["governance_missing"] = missing_sections
        if missing_sections:
            return {
                "name": f"model:{model_id}",
                "ok": False,
                "detail": {**detail, "error": f"governance sidecar missing sections: {', '.join(missing_sections)}"},
            }

    task = str(config.get("task") or config.get("type") or "").lower()
    output = config.get("output") if isinstance(config.get("output"), dict) else {}
    if task in {"detection", "classification"} and "classes" not in output and not label_file.is_file():
        if strict_sidecars or strict_governance:
            return {"name": f"model:{model_id}", "ok": False, "detail": {**detail, "error": "labels file missing"}}

    return {"name": f"model:{model_id}", "ok": True, "detail": detail}

def validate_config(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).resolve()
    models_root = Path(getattr(args, "models_root", None) or "models").resolve()
    capabilities_path = Path(getattr(args, "capabilities", "model-capabilities.yml")).resolve()
    strict_hash = bool(getattr(args, "strict_hash", False))
    strict_sidecars = bool(getattr(args, "strict_sidecars", False))
    strict_governance = bool(getattr(args, "strict_governance", False))
    allow_missing_artifacts = bool(getattr(args, "allow_missing_artifacts", False))
    selected_ids = [str(item) for item in getattr(args, "model_id", []) or []]

    payload = load_yaml(config_path)
    models = payload.get("models", payload) if isinstance(payload.get("models", payload), dict) else {}
    aliases = payload.get("aliases", {}) if isinstance(payload.get("aliases", {}), dict) else {}
    capabilities_payload = load_yaml(capabilities_path)
    capabilities = capabilities_payload.get("capabilities", capabilities_payload) if isinstance(capabilities_payload.get("capabilities", capabilities_payload), dict) else {}

    if selected_ids:
        selected_ids = resolve_model_ids(selected_ids, aliases)
    else:
        selected_ids = active_model_ids(models, aliases, capabilities)

    checks: list[dict[str, Any]] = []
    for model_id in selected_ids:
        config = models.get(model_id)
        if isinstance(config, dict):
            checks.append(
                validate_model(
                    model_id,
                    config,
                    models_root,
                    strict_hash=strict_hash,
                    strict_sidecars=strict_sidecars,
                    strict_governance=strict_governance,
                    allow_missing_artifacts=allow_missing_artifacts,
                )
            )
        else:
            checks.append({"name": f"model:{model_id}", "ok": False, "detail": {"model_id": model_id, "error": "model config missing"}})

    errors: list[str] = []
    warnings: list[str] = []
    for item in checks:
        detail = item.get("detail", {})
        if not item["ok"]:
            error = detail.get("error") if isinstance(detail, dict) else None
            errors.append(f"{item['name']}: {error or 'validation failed'}")
        elif isinstance(detail, dict) and detail.get("artifact_present") is False:
            warnings.append(f"{item['name']}: model artifact missing; validated metadata only")

    return {
        "ok": not errors,
        "config_path": str(config_path),
        "models_root": str(models_root),
        "capabilities_path": str(capabilities_path),
        "model_count": len(checks),
        "models": checks,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production model governance sidecars.")
    parser.add_argument("--config", default="models.yml", help="Path to models.yml.")
    parser.add_argument("--models-root", default="models", help="Model artifact root.")
    parser.add_argument("--capabilities", default="model-capabilities.yml", help="Optional capabilities file.")
    parser.add_argument("--model-id", action="append", help="Only validate these model keys or aliases.")
    parser.add_argument("--strict-hash", action="store_true", help="Require artifact sha256.")
    parser.add_argument("--strict-sidecars", action="store_true", help="Require model cards and labels where applicable.")
    parser.add_argument("--strict-governance", action="store_true", help="Require governance sidecars and sections.")
    parser.add_argument("--allow-missing-artifacts", action="store_true", help="Validate metadata even when model binaries are distributed out of band.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report = validate_config(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"model governance check: {'OK' if report['ok'] else 'FAILED'}")
        for item in report["models"]:
            print(f"{'ok' if item['ok'] else 'fail'}: {item['name']}")
            if not item["ok"]:
                print(f"  detail: {item['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
