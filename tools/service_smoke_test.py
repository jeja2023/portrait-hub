"""HTTP smoke test for a running gpu-services worker or gateway."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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


def request_json(base_url: str, path: str, token: str | None, timeout: float, tenant_id: str = "default") -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    headers = {"Accept": "application/json", "X-Tenant-ID": tenant_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-API-Key"] = token
    request = Request(url, headers=headers)
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


REQUIRED_OPENAPI_PATHS = {"/health", "/ready", "/predict", "/vision/infer", "/vision/batch-infer"}


def check_json_endpoint(
    report: SmokeReport,
    name: str,
    base_url: str,
    path: str,
    token: str | None,
    timeout: float,
    expected_status: set[int],
    tenant_id: str = "default",
) -> Any:
    try:
        status, payload = request_json(base_url, path, token, timeout, tenant_id)
    except (TimeoutError, URLError) as exc:
        report.add(name, False, f"request failed: {exc}")
        return None
    ok = status in expected_status
    report.add(name, ok, {"status": status, "payload": payload})
    return payload


def check_openapi(report: SmokeReport, base_url: str, token: str | None, timeout: float, required: bool, tenant_id: str = "default") -> None:
    expected_status = {200} if required else {200, 404}
    openapi = check_json_endpoint(report, "openapi", base_url, "/openapi.json", token, timeout, expected_status, tenant_id)
    openapi_status = report.checks[-1]["detail"]["status"] if report.checks else None
    if openapi_status == 200 and isinstance(openapi, dict) and isinstance(openapi.get("paths"), dict):
        paths = set(openapi.get("paths", {}))
        missing = sorted(REQUIRED_OPENAPI_PATHS - paths)
        report.add("openapi_required_paths", not missing, {"missing": missing})
    elif required:
        report.add("openapi_required_paths", False, "openapi.json did not return an OpenAPI document")
    else:
        report.add("openapi_optional", True, "openapi.json is disabled or not a JSON document")


def run_smoke(args: argparse.Namespace) -> SmokeReport:
    report = SmokeReport()
    health = check_json_endpoint(report, "health", args.base_url, "/health", args.token, args.timeout, {200}, args.tenant_id)
    if isinstance(health, dict) and health.get("status") == "healthy":
        report.add("health_status", True, health.get("status") if isinstance(health, dict) else health)
    else:
        report.add("health_status", False, health)

    check_openapi(report, args.base_url, args.token, args.timeout, args.check_openapi, args.tenant_id)

    check_json_endpoint(report, "metrics", args.base_url, "/metrics", args.token, args.timeout, {200}, args.tenant_id)

    if args.require_ready:
        check_json_endpoint(report, "ready", args.base_url, "/ready", args.token, args.timeout, {200}, args.tenant_id)
    else:
        check_json_endpoint(report, "ready_optional", args.base_url, "/ready", args.token, args.timeout, {200, 503}, args.tenant_id)

    if args.deep_ready:
        query = urlencode(
            {
                "load_models": "true" if args.load_models else "false",
                "dummy_inference": "true" if args.dummy_inference else "false",
            }
        )
        check_json_endpoint(report, "ready_deep", args.base_url, f"/ready/deep?{query}", args.token, args.timeout, {200}, args.tenant_id)

    for model_id in args.model_id or []:
        query_args = {"model_id": model_id}
        if args.traffic_key:
            query_args["traffic_key"] = args.traffic_key
        query = urlencode(query_args)
        check_json_endpoint(report, f"model_package:{model_id}", args.base_url, f"/model-package?{query}", args.token, args.timeout, {200}, args.tenant_id)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a smoke test against a running gpu-services endpoint.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9001", help="Service base URL.")
    parser.add_argument("--token", default=None, help="API token for protected endpoints.")
    parser.add_argument("--tenant-id", default="default", help="Tenant id sent as X-Tenant-ID for tenant-scoped endpoints.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds.")
    parser.add_argument("--require-ready", action="store_true", help="Fail if /ready is not CUDA-ready.")
    parser.add_argument("--check-openapi", action="store_true", help="Require /openapi.json to be enabled and contain core paths.")
    parser.add_argument("--deep-ready", action="store_true", help="Call /ready/deep.")
    parser.add_argument("--load-models", action="store_true", help="Ask /ready/deep to load configured models.")
    parser.add_argument("--dummy-inference", action="store_true", help="Ask /ready/deep to run dummy inference.")
    parser.add_argument("--model-id", action="append", help="Check /model-package for this model id or alias.")
    parser.add_argument("--traffic-key", default=None, help="Traffic key to resolve weighted aliases in /model-package checks.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report = run_smoke(args)
    if args.json:
        print(json.dumps({"ok": report.ok, "checks": report.checks}, ensure_ascii=False, indent=2))
    else:
        print(f"service smoke test: {'OK' if report.ok else 'FAILED'}")
        for item in report.checks:
            marker = "ok" if item["ok"] else "fail"
            print(f"{marker}: {item['name']}")
            if not item["ok"]:
                print(f"  detail: {item['detail']}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
