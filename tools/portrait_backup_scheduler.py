from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


def post_backup(base_url: str, *, token: str | None, tenant_id: str, updated_since: float | None, timeout: float) -> dict[str, Any]:
    payload: dict[str, Any] = {"confirm": "backup"}
    if updated_since is not None:
        payload["updated_since"] = updated_since
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/admin/backup",
        data=data,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-tenant-id": tenant_id,
        },
    )
    if token:
        request.add_header("authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def run_scheduler(
    *,
    base_url: str,
    token: str | None,
    tenant_id: str,
    interval_seconds: float,
    once: bool,
    timeout: float,
) -> int:
    updated_since: float | None = None
    while True:
        started_at = time.time()
        try:
            payload = post_backup(base_url, token=token, tenant_id=tenant_id, updated_since=updated_since, timeout=timeout)
            print(json.dumps({"ok": True, "backup": payload.get("data", {}), "updated_since": updated_since}, ensure_ascii=False, sort_keys=True))
            updated_since = started_at
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": type(exc).__name__}, ensure_ascii=False, sort_keys=True))
            if once:
                return 1
        if once:
            return 0
        time.sleep(max(1.0, interval_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description="周期性运行 PortraitHub 管理备份。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=None)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--interval-seconds", type=float, default=3600.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run_scheduler(
        base_url=args.base_url,
        token=args.token,
        tenant_id=args.tenant_id,
        interval_seconds=args.interval_seconds,
        once=args.once,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
