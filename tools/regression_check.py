"""对运行中的推理服务运行固定样本回归测试的脚本。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tools.report_redaction import safe_report_repr


@dataclass
class CompareResult:
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        if path.suffix.lower() == ".json":
            raw = json.load(file)
        else:
            raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError("清单根节点必须是映射")
    return raw


def manifest_relative_path(base_dir: Path, raw_path: str, field_name: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError(f"{field_name} 必须相对于回归清单目录")
    base = base_dir.resolve()
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须位于回归清单目录内") from exc
    return resolved


def load_expected(case: dict[str, Any], base_dir: Path) -> Any:
    if "expected" in case:
        return case["expected"]
    expected_path = case.get("expected_path")
    if not isinstance(expected_path, str):
        return None
    path = manifest_relative_path(base_dir, expected_path, "case.expected_path")
    with path.open("r", encoding="utf-8") as file:
        if path.suffix.lower() == ".json":
            return json.load(file)
        return yaml.safe_load(file)


def compare_values(
    actual: Any,
    expected: Any,
    result: CompareResult,
    path: str = "$",
    tolerance: float = 1e-6,
) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            result.error(path, f"expected mapping, got {type(actual).__name__}")
            return
        for key, expected_value in expected.items():
            if key not in actual:
                result.error(f"{path}.{key}", "missing key")
                continue
            compare_values(actual[key], expected_value, result, f"{path}.{key}", tolerance)
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            result.error(path, f"expected list, got {type(actual).__name__}")
            return
        if len(actual) < len(expected):
            result.error(path, f"expected at least {len(expected)} items, got {len(actual)}")
            return
        for index, expected_value in enumerate(expected):
            compare_values(actual[index], expected_value, result, f"{path}[{index}]", tolerance)
        return

    if isinstance(expected, float):
        if not isinstance(actual, (int, float)):
            result.error(path, f"expected number, got {type(actual).__name__}")
            return
        if abs(float(actual) - expected) > tolerance:
            result.error(path, f"expected {expected} +/- {tolerance}, got {actual}")
        return

    if actual != expected:
        result.error(path, f"expected {safe_report_repr(expected, path)}, got {safe_report_repr(actual, path)}")


def open_case_files(case: dict[str, Any], base_dir: Path) -> tuple[list[tuple[str, tuple[str, Any, str]]], list[Any]]:
    handles = []
    file_items = []
    files = case.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError("case.files 必须是表单字段到路径或路径列表的映射")

    try:
        for field, raw_paths in files.items():
            paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
            for raw_path in paths:
                if not isinstance(raw_path, str):
                    raise ValueError(f"字段 {field} 的文件路径必须是字符串")
                path = manifest_relative_path(base_dir, raw_path, f"case.files.{field}")
                handle = path.open("rb")
                handles.append(handle)
                file_items.append((field, (path.name, handle, "application/octet-stream")))
    except Exception:
        for handle in handles:
            handle.close()
        raise
    return file_items, handles


def normalize_auth_scheme(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in {"bearer", "api-key"}:
        raise ValueError("auth_scheme 必须是 'bearer' 或 'api-key'")
    return normalized


def apply_auth_headers(headers: dict[str, Any], token: str | None, auth_scheme: str) -> None:
    if not token:
        return
    if normalize_auth_scheme(auth_scheme) == "api-key":
        headers.setdefault("X-API-Key", token)
    else:
        headers.setdefault("Authorization", f"Bearer {token}")


def run_case(
    client: Any,
    base_url: str,
    token: str | None,
    tenant_id: str,
    case: dict[str, Any],
    base_dir: Path,
    auth_scheme: str = "bearer",
) -> dict[str, Any]:
    method = str(case.get("method", "GET")).upper()
    path = str(case.get("path") or case.get("endpoint") or "")
    if not path.startswith("/"):
        raise ValueError("case.path 必须以 / 开头")

    headers = dict(case.get("headers") or {})
    if tenant_id:
        headers.setdefault("X-Tenant-ID", tenant_id)
    apply_auth_headers(headers, token, str(case.get("auth_scheme", auth_scheme)))

    files, handles = open_case_files(case, base_dir)
    try:
        response = client.request(
            method,
            base_url.rstrip("/") + path,
            params=case.get("query"),
            headers=headers,
            json=case.get("json"),
            data=case.get("form"),
            files=files or None,
        )
    finally:
        for handle in handles:
            handle.close()

    try:
        payload = response.json()
    except Exception:
        payload = response.text
    return {"status_code": response.status_code, "payload": payload}


def run_regression(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx 为必填项。运行回归检查前请安装 requirements/dev.txt。") from exc

    manifest_path = Path(args.manifest).resolve()
    manifest = load_manifest(manifest_path)
    base_dir = manifest_path.parent
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("清单必须包含 cases 列表")
    tenant_id = str(manifest.get("tenant_id", args.tenant_id))

    results = []
    with httpx.Client(timeout=args.timeout) as client:
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                raise ValueError(f"第 {index} 个用例必须是映射")
            name = str(case.get("name") or f"case_{index}")
            expected_status = int(case.get("expected_status", 200))
            tol_val = case.get("tolerance")
            if tol_val is None:
                tol_val = manifest.get("tolerance")
            if tol_val is None:
                tol_val = args.tolerance
            tolerance = float(tol_val) if tol_val is not None else 1e-6
            case_tenant_id = str(case.get("tenant_id", tenant_id))
            actual = run_case(client, args.base_url, args.token, case_tenant_id, case, base_dir, args.auth_scheme)
            comparison = CompareResult()
            if actual["status_code"] != expected_status:
                comparison.error("$status_code", f"expected {expected_status}, got {actual['status_code']}")
            expected = load_expected(case, base_dir)
            if expected is not None:
                compare_values(actual["payload"], expected, comparison, tolerance=tolerance)
            results.append(
                {
                    "name": name,
                    "ok": comparison.ok,
                    "status_code": actual["status_code"],
                    "errors": comparison.errors,
                }
            )

    return {"ok": all(item["ok"] for item in results), "case_count": len(results), "cases": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="针对运行中的 gpu-services 端点执行固定回归用例。")
    parser.add_argument("--manifest", required=True, help="回归清单 YAML/JSON。")
    parser.add_argument("--base-url", default="http://127.0.0.1:9001", help="服务基础 URL。")
    parser.add_argument("--token", default=None, help="受保护端点的 API 令牌。")
    parser.add_argument("--auth-scheme", choices=["bearer", "api-key"], default="bearer", help="--token 的发送方式，除非用例覆盖 auth_scheme。")
    parser.add_argument("--tenant-id", default="default", help="作为 X-Tenant-ID 发送的默认租户 ID。")
    parser.add_argument("--timeout", type=float, default=30.0, help="请求超时时间（秒）。")
    parser.add_argument("--tolerance", type=float, default=1e-6, help="默认浮点比较容差。")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    args = parser.parse_args()

    report = run_regression(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"回归检查：{'通过' if report['ok'] else '失败'}")
        for item in report["cases"]:
            marker = "通过" if item["ok"] else "失败"
            print(f"{marker}: {item['name']} 状态码={item['status_code']}")
            for error in item["errors"]:
                print(f"  {error}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
