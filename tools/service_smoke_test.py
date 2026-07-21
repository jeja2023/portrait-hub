"""针对运行中的 PortraitHub 工作节点或网关的 HTTP 冒烟测试脚本。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from tools.report_redaction import redact_for_report


@dataclass
class SmokeReport:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": redact_for_report(detail)})

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.checks)


def normalize_auth_scheme(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in {"bearer", "api-key"}:
        raise ValueError("auth_scheme 必须是 'bearer' 或 'api-key'")
    return normalized


def auth_headers(token: str | None, tenant_id: str = "default", auth_scheme: str = "bearer") -> dict[str, str]:
    headers = {"Accept": "application/json", "X-Tenant-ID": tenant_id}
    if token:
        if normalize_auth_scheme(auth_scheme) == "api-key":
            headers["X-API-Key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(
    base_url: str,
    path: str,
    token: str | None,
    timeout: float,
    tenant_id: str = "default",
    auth_scheme: str = "bearer",
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    request = Request(url, headers=auth_headers(token, tenant_id, auth_scheme))
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            try:
                payload = json.loads(body) if body else None
            except json.JSONDecodeError:
                payload = body
            return response.status, payload
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = body
        return exc.code, detail


REQUIRED_OPENAPI_PATHS = {
    "/health",
    "/ready",
    "/predict",
    "/v1/vision/infer",
    "/v1/models",
    "/v1/jobs/video",
    "/v1/streams",
}


def check_json_endpoint(
    report: SmokeReport,
    name: str,
    base_url: str,
    path: str,
    token: str | None,
    timeout: float,
    expected_status: set[int],
    tenant_id: str = "default",
    auth_scheme: str = "bearer",
) -> Any:
    try:
        status, payload = request_json(base_url, path, token, timeout, tenant_id, auth_scheme)
    except (TimeoutError, URLError) as exc:
        report.add(name, False, f"请求失败: {exc}")
        return None
    ok = status in expected_status
    report.add(name, ok, {"status": status, "payload": payload})
    return payload


def check_openapi(
    report: SmokeReport,
    base_url: str,
    token: str | None,
    timeout: float,
    required: bool,
    tenant_id: str = "default",
    auth_scheme: str = "bearer",
) -> None:
    expected_status = {200} if required else {200, 404}
    openapi = check_json_endpoint(
        report,
        "openapi",
        base_url,
        "/openapi.json",
        token,
        timeout,
        expected_status,
        tenant_id,
        auth_scheme,
    )
    openapi_status = report.checks[-1]["detail"]["status"] if report.checks else None
    if openapi_status == 200 and isinstance(openapi, dict) and isinstance(openapi.get("paths"), dict):
        paths = set(openapi.get("paths", {}))
        missing = sorted(REQUIRED_OPENAPI_PATHS - paths)
        report.add("openapi_required_paths", not missing, {"missing": missing})
    elif required:
        report.add("openapi_required_paths", False, "openapi.json 未返回开放接口定义文档")
    else:
        report.add("openapi_optional", True, "openapi.json is disabled or not a JSON document")


def run_smoke(args: argparse.Namespace) -> SmokeReport:
    report = SmokeReport()
    health = check_json_endpoint(
        report,
        "health",
        args.base_url,
        "/health",
        args.token,
        args.timeout,
        {200},
        args.tenant_id,
        args.auth_scheme,
    )
    if isinstance(health, dict) and health.get("status") == "healthy":
        report.add(
            "health_status",
            True,
            health.get("status") if isinstance(health, dict) else health,
        )
    else:
        report.add("health_status", False, health)

    check_openapi(
        report,
        args.base_url,
        args.token,
        args.timeout,
        args.check_openapi,
        args.tenant_id,
        args.auth_scheme,
    )

    check_json_endpoint(
        report,
        "metrics",
        args.base_url,
        "/metrics",
        args.token,
        args.timeout,
        {200},
        args.tenant_id,
        args.auth_scheme,
    )

    if args.require_ready:
        check_json_endpoint(
            report,
            "ready",
            args.base_url,
            "/ready",
            args.token,
            args.timeout,
            {200},
            args.tenant_id,
            args.auth_scheme,
        )
    else:
        check_json_endpoint(
            report,
            "ready_optional",
            args.base_url,
            "/ready",
            args.token,
            args.timeout,
            {200, 503},
            args.tenant_id,
            args.auth_scheme,
        )

    if args.deep_ready:
        query = urlencode(
            {
                "load_models": "true" if args.load_models else "false",
                "dummy_inference": "true" if args.dummy_inference else "false",
            }
        )
        check_json_endpoint(
            report,
            "ready_deep",
            args.base_url,
            f"/ready/deep?{query}",
            args.token,
            args.timeout,
            {200},
            args.tenant_id,
            args.auth_scheme,
        )

    for model_id in args.model_id or []:
        path = f"/v1/models/{quote(model_id, safe='')}"
        if args.traffic_key:
            path = f"{path}?{urlencode({'traffic_key': args.traffic_key})}"
        check_json_endpoint(
            report,
            f"model:{model_id}",
            args.base_url,
            path,
            args.token,
            args.timeout,
            {200},
            args.tenant_id,
            args.auth_scheme,
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="针对运行中的 PortraitHub 端点执行冒烟测试。")
    parser.add_argument("--base-url", default="http://127.0.0.1:9001", help="服务基础 URL。")
    parser.add_argument("--token", default=None, help="受保护端点的 API 令牌。")
    parser.add_argument(
        "--auth-scheme",
        choices=["bearer", "api-key"],
        default="bearer",
        help="--token 的发送方式。",
    )
    parser.add_argument(
        "--tenant-id",
        default="default",
        help="租户范围端点中作为 X-Tenant-ID 发送的租户 ID。",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="请求超时时间（秒）。")
    parser.add_argument("--require-ready", action="store_true", help="/ready 未达到运行时就绪时失败。")
    parser.add_argument(
        "--check-openapi",
        action="store_true",
        help="要求 /openapi.json 已启用且包含核心路径。",
    )
    parser.add_argument("--deep-ready", action="store_true", help="调用 /ready/deep。")
    parser.add_argument("--load-models", action="store_true", help="要求 /ready/deep 加载已配置模型。")
    parser.add_argument("--dummy-inference", action="store_true", help="要求 /ready/deep 执行模拟推理。")
    parser.add_argument(
        "--model-id",
        action="append",
        help="检查此模型 ID 或别名的 /v1/models/{model_id}。",
    )
    parser.add_argument(
        "--traffic-key",
        default=None,
        help="/v1/models/{model_id} 检查中解析加权别名使用的流量键。",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    args = parser.parse_args()

    report = run_smoke(args)
    if args.json:
        print(json.dumps({"ok": report.ok, "checks": report.checks}, ensure_ascii=False, indent=2))
    else:
        print(f"服务冒烟测试：{'通过' if report.ok else '失败'}")
        for item in report.checks:
            marker = "通过" if item["ok"] else "失败"
            print(f"{marker}: {item['name']}")
            if not item["ok"]:
                print(f"  详情: {item['detail']}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
