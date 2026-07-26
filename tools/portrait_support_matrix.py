from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_MATRIX = Path(__file__).resolve().parents[1] / "deploy" / "support-matrix.json"
DEFAULT_MARKDOWN = Path(__file__).resolve().parents[1] / "docs" / "deployment" / "SUPPORT_MATRIX.md"
SUPPORT_LEVELS = {"supported", "limited", "experimental", "unsupported"}
REQUIRED_PROFILES = {"development", "private_standard", "private_ha", "platform_api"}
REQUIRED_INSTALL_MODES = {"online", "offline", "proxy-network", "air-gapped"}
REQUIRED_DATA_SERVICES = {"postgres-pgvector", "qdrant", "redis", "minio-s3", "otel-collector", "reverse-proxy"}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class SupportMatrixError(ValueError):
    pass


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_matrix(path: Path = DEFAULT_MATRIX) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupportMatrixError(f"support matrix cannot be read: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise SupportMatrixError("support matrix root must be an object")
    return payload


def _indexed_rows(payload: dict[str, Any], key: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list) or not rows:
        errors.append(f"{key} must be a non-empty array")
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            errors.append(f"{key}[{position}] must have a non-empty id")
            continue
        row_id = row["id"]
        if row_id in indexed:
            errors.append(f"{key} contains duplicate id: {row_id}")
        indexed[row_id] = row
        if row.get("support_level") not in SUPPORT_LEVELS:
            errors.append(f"{key}.{row_id} has invalid support_level")
    return indexed


def validate_matrix(payload: dict[str, Any], *, root: Path | None = None) -> list[str]:
    repository_root = root or Path(__file__).resolve().parents[1]
    errors: list[str] = []
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    product = payload.get("product")
    if not isinstance(product, dict):
        errors.append("product must be an object")
        product = {}
    current = str(product.get("version") or "")
    previous = str(product.get("previous_stable_version") or "")
    if not SEMVER.fullmatch(current) or not SEMVER.fullmatch(previous) or current == previous:
        errors.append("product current and previous stable versions must be distinct semantic versions")
    try:
        project = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        if current != project["version"]:
            errors.append("support matrix product version does not match pyproject.toml")
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"cannot validate project version: {type(exc).__name__}")
    if previous and not (repository_root / "docs" / "releases" / f"{previous}.md").is_file():
        errors.append("previous stable release notes are missing")

    operating_systems = _indexed_rows(payload, "operating_systems", errors)
    accelerators = _indexed_rows(payload, "accelerators", errors)
    container_platforms = _indexed_rows(payload, "container_platforms", errors)
    data_services = _indexed_rows(payload, "data_services", errors)
    installation_modes = _indexed_rows(payload, "installation_modes", errors)
    hardware_profiles = _indexed_rows(payload, "hardware_profiles", errors)
    profiles = _indexed_rows(payload, "profiles", errors)
    if set(profiles) != REQUIRED_PROFILES:
        errors.append("profiles must define development, private_standard, private_ha and platform_api exactly")
    if not REQUIRED_INSTALL_MODES.issubset(installation_modes):
        errors.append("installation_modes is missing online/offline/proxy-network/air-gapped")
    if not REQUIRED_DATA_SERVICES.issubset(data_services):
        errors.append("data_services is incomplete")

    references = {
        "operating_system_ids": operating_systems,
        "accelerator_ids": accelerators,
        "container_platform_ids": container_platforms,
        "data_service_ids": data_services,
        "installation_mode_ids": installation_modes,
    }
    for profile_id, profile in profiles.items():
        for field, known in references.items():
            values = profile.get(field)
            if not isinstance(values, list):
                errors.append(f"profiles.{profile_id}.{field} must be an array")
                continue
            unknown = sorted({str(item) for item in values} - set(known))
            if unknown:
                errors.append(f"profiles.{profile_id}.{field} has unknown ids: {', '.join(unknown)}")
        hardware_id = profile.get("hardware_profile_id")
        if hardware_id is not None and hardware_id not in hardware_profiles:
            errors.append(f"profiles.{profile_id}.hardware_profile_id is unknown")
        limits = profile.get("limits")
        required_limits = {
            "max_loaded_models",
            "max_projects",
            "max_concurrency",
            "max_streams",
            "max_data_gib",
            "qualification_status",
        }
        if not isinstance(limits, dict) or not required_limits.issubset(limits):
            errors.append(f"profiles.{profile_id}.limits must explicitly define all capacity boundaries")
        blockers = profile.get("blockers")
        if profile.get("support_level") != "supported" and (not isinstance(blockers, list) or not blockers):
            errors.append(f"profiles.{profile_id} must explain non-supported status with blockers")
        if profile_id != "development" and profile.get("commercial_sla") is True:
            if profile.get("support_level") != "supported" or blockers:
                errors.append(f"profiles.{profile_id} cannot enable commercial_sla while blockers remain")

    upgrade_paths = payload.get("upgrade_paths")
    expected_path = {"from": previous, "to": current}
    if not isinstance(upgrade_paths, list) or not any(
        isinstance(item, dict) and all(item.get(key) == value for key, value in expected_path.items())
        for item in upgrade_paths
    ):
        errors.append("upgrade_paths must include the N-1 to N path")

    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        errors.append("runtime must be an object")
    else:
        dockerfile = (repository_root / "Dockerfile").read_text(encoding="utf-8")
        cpu_dockerfile = (repository_root / "Dockerfile.cpu").read_text(encoding="utf-8")
        for field, source in (
            ("gpu_image", dockerfile),
            ("frontend_builder", dockerfile),
            ("cpu_image", cpu_dockerfile),
        ):
            if str(runtime.get(field) or "") not in source:
                errors.append(f"runtime.{field} does not match the release Dockerfiles")
    return errors


def matrix_status(payload: dict[str, Any], *, target_profile: str | None = None, root: Path | None = None) -> dict[str, Any]:
    errors = validate_matrix(payload, root=root)
    result: dict[str, Any] = {
        "ok": not errors,
        "schema_version": payload.get("schema_version"),
        "product_version": (payload.get("product") or {}).get("version"),
        "sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
        "errors": errors,
    }
    if target_profile is not None:
        profile = next((item for item in payload.get("profiles", []) if item.get("id") == target_profile), None)
        if profile is None:
            result["ok"] = False
            result["profile_error"] = f"unknown target profile: {target_profile}"
        else:
            blockers = list(profile.get("blockers") or [])
            result["profile"] = target_profile
            result["support_level"] = profile.get("support_level")
            result["commercial_sla"] = bool(profile.get("commercial_sla"))
            result["blockers"] = blockers
            result["commercial_ready"] = (
                not errors
                and profile.get("support_level") == "supported"
                and bool(profile.get("commercial_sla"))
                and not blockers
            )
    return result


def render_markdown(payload: dict[str, Any]) -> str:
    status = matrix_status(payload)
    if not status["ok"]:
        raise SupportMatrixError("cannot render an invalid support matrix: " + "; ".join(status["errors"]))
    product = payload["product"]
    lines = [
        "# PortraitHub 支持矩阵",
        "",
        f"产品版本：`{product['version']}`；上一稳定版本：`{product['previous_stable_version']}`；机器源摘要：`{status['sha256']}`。",
        "",
        "> `supported` 表示在列明边界内支持；`limited` 必须完成所列验收后才能进入合同 SLA；`experimental` 仅供试验；`unsupported` 禁止生产使用。",
        "",
        "## 交付形态",
        "",
        "| 形态 | 状态 | 商业 SLA | 拓扑 | 容量边界 | 阻断项 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for profile in payload["profiles"]:
        limits = profile["limits"]
        boundary = str(limits["qualification_status"])
        blockers = "；".join(profile.get("blockers") or ["无"])
        lines.append(
            f"| `{profile['id']}` | `{profile['support_level']}` | {'是' if profile['commercial_sla'] else '否'} | "
            f"{profile['topology']} | `{boundary}` | {blockers} |"
        )
    lines.extend(["", "## 操作系统", "", "| 标识 | 系统 | 架构 | 状态 | 约束 |", "| --- | --- | --- | --- | --- |"]) 
    for item in payload["operating_systems"]:
        lines.append(
            f"| `{item['id']}` | {item['name']} | `{item['architecture']}` | `{item['support_level']}` | {item.get('restriction', '')} |"
        )
    lines.extend(["", "## GPU 与运行时", "", f"GPU 镜像基线：`{payload['runtime']['gpu_image']}`；CPU 镜像基线：`{payload['runtime']['cpu_image']}`。", "", "| 标识 | 设备范围 | 状态 | 约束 |", "| --- | --- | --- | --- |"]) 
    for item in payload["accelerators"]:
        lines.append(
            f"| `{item['id']}` | {item['device_range']} | `{item['support_level']}` | {item.get('restriction', '')} |"
        )
    lines.extend(["", "## 数据服务", "", "| 组件 | 版本 | 状态 | 证据或约束 |", "| --- | --- | --- | --- |"]) 
    for item in payload["data_services"]:
        detail = item.get("evidence") or item.get("restriction") or ""
        lines.append(f"| `{item['id']}` | {item['version']} | `{item['support_level']}` | {detail} |")
    lines.extend(["", "## 安装与升级", "", "| 模式 | 状态 | 必需证据 |", "| --- | --- | --- |"]) 
    for item in payload["installation_modes"]:
        lines.append(f"| `{item['id']}` | `{item['support_level']}` | {item['evidence_required']} |")
    lines.extend(
        [
            "",
            f"当前正式升级路径为 `{product['previous_stable_version']} -> {product['version']}`。回退必须遵守发布说明的数据边界；回滚窗口内只做 expand，不删除旧表或旧字段。",
            "",
            "## 候选硬件",
            "",
            "候选硬件只用于容量测试起点，不构成吞吐、延迟或可用性承诺。模型、数据规模、并发和流数量的最大值保持为空，直到目标环境的签名容量报告通过。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and render the PortraitHub release support matrix.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--target-profile", choices=sorted(REQUIRED_PROFILES))
    parser.add_argument("--render", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = load_matrix(args.matrix)
        result = matrix_status(payload, target_profile=args.target_profile)
        if args.render is not None and result["ok"]:
            args.render.parent.mkdir(parents=True, exist_ok=True)
            args.render.write_text(render_markdown(payload), encoding="utf-8")
            result["rendered"] = str(args.render)
    except SupportMatrixError as exc:
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
