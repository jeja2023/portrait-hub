"""在多个 gpu-services 工作节点上执行运维操作的控制脚本。"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from tools.report_redaction import redact_for_report


def normalize_auth_scheme(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in {"bearer", "api-key"}:
        raise ValueError("auth_scheme 必须是 'bearer' 或 'api-key'")
    return normalized



def auth_headers(token: str | None, tenant_id: str = "default", auth_scheme: str = "bearer") -> dict[str, str]:
    headers = {"X-Tenant-ID": tenant_id}
    if token:
        if normalize_auth_scheme(auth_scheme) == "api-key":
            headers["X-API-Key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def split_model_id(model_id: str) -> dict[str, str]:
    if "/" not in model_id:
        raise ValueError(f"模型必须使用 project/model.onnx 格式：{model_id}")
    project_name, model_name = model_id.split("/", 1)
    if not project_name or not model_name:
        raise ValueError(f"模型必须使用 project/model.onnx 格式：{model_id}")
    for part in (project_name, model_name):
        if part.strip() != part or part in {".", ".."} or "/" in part or "\\" in part:
            raise ValueError("模型项目和模型名称不能包含路径分隔符、首尾空白或相对路径片段")
    return {"project_name": project_name, "model_name": model_name}


def request_worker(base_url: str, args: argparse.Namespace) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx 为必填项。运行 worker 控制前请安装 requirements/dev.txt。") from exc

    base_url = base_url.rstrip("/")
    headers = auth_headers(args.token, args.tenant_id, args.auth_scheme)
    timeout = args.timeout
    action = args.action
    with httpx.Client(timeout=timeout) as client:
        if action == "health":
            response = client.get(f"{base_url}/health", headers=headers)
        elif action == "ready":
            response = client.get(f"{base_url}/ready", headers=headers)
        elif action == "reload-config":
            response = client.post(f"{base_url}/reload-config", headers=headers)
        elif action == "aliases":
            response = client.get(f"{base_url}/rollout/aliases", headers=headers)
        elif action == "warmup":
            models = [split_model_id(item) for item in args.model]
            response = client.post(f"{base_url}/warmup", headers=headers, json={"models": models})
        elif action in {"reload", "unload"}:
            if len(args.model) != 1:
                raise ValueError(f"{action} 需要且只能提供一个 --model")
            response = client.post(f"{base_url}/{action}", headers=headers, json=split_model_id(args.model[0]))
        else:
            raise ValueError(f"不支持的操作：{action}")

    try:
        payload = response.json()
    except Exception:
        payload = response.text
    return {
        "base_url": base_url,
        "action": action,
        "ok": 200 <= response.status_code < 300,
        "status_code": response.status_code,
        "payload": redact_for_report(payload),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="在多个 gpu-services worker 上执行运维操作。")
    parser.add_argument(
        "--base-url",
        action="append",
        default=[],
        help="worker 基础 URL。可重复传入，默认 9001 和 9002。",
    )
    parser.add_argument("--token", default=None, help="受保护端点的 API 令牌。")
    parser.add_argument("--auth-scheme", choices=["bearer", "api-key"], default="bearer", help="--token 的发送方式。")
    parser.add_argument("--tenant-id", default="default", help="作为 X-Tenant-ID 发送的租户 ID。")
    parser.add_argument("--timeout", type=float, default=30.0, help="请求超时时间（秒）。")
    parser.add_argument(
        "--action",
        choices=["health", "ready", "reload-config", "aliases", "warmup", "reload", "unload"],
        required=True,
        help="要在每个 worker 上执行的操作。",
    )
    parser.add_argument("--model", action="append", default=[], help="用于 warmup/reload/unload 的模型 ID，格式为 project/model.onnx。")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    args = parser.parse_args()

    base_urls = args.base_url or ["http://127.0.0.1:9001", "http://127.0.0.1:9002"]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(base_urls)) as executor:
        futures = {executor.submit(request_worker, base_url, args): base_url for base_url in base_urls}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"base_url": futures[future], "action": args.action, "ok": False, "error": redact_for_report(str(exc))})

    ok = all(bool(item.get("ok")) for item in results)
    sorted_results = sorted(results, key=lambda item: str(item.get("base_url", "")))
    report = {"ok": ok, "results": sorted_results}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"worker 控制：{'通过' if ok else '失败'}")
        for item in sorted_results:
            marker = "通过" if item["ok"] else "失败"
            print(f"{marker}: {item['base_url']} 操作={item['action']}")
            if not item["ok"]:
                print(f"  详情: {item.get('error') or item.get('payload')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
