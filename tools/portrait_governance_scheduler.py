from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from tools.portrait_backup_scheduler import post_backup


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def post_retention_cleanup(
    base_url: str,
    *,
    token: str | None,
    tenant_id: str,
    retention_days: int,
    timeout: float,
) -> dict[str, Any]:
    data = json.dumps({"retention_days": retention_days, "confirm": "cleanup"}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/admin/retention/cleanup",
        data=data,
        method="POST",
        headers={"content-type": "application/json", "x-tenant-id": tenant_id},
    )
    if token:
        request.add_header("authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def run_governance_scheduler(
    *,
    base_url: str,
    token: str | None,
    tenant_id: str,
    backup_interval_seconds: float,
    cleanup_interval_seconds: float,
    retention_days: int,
    once: bool,
    timeout: float,
) -> int:
    updated_since: float | None = None
    last_backup_at = 0.0
    last_cleanup_at = 0.0
    while True:
        now = time.time()
        ok = True
        if once or now - last_backup_at >= backup_interval_seconds:
            started_at = now
            try:
                payload = post_backup(base_url, token=token, tenant_id=tenant_id, updated_since=updated_since, timeout=timeout)
                print(json.dumps({"task": "backup", "ok": True, "data": payload.get("data", {}), "updated_since": updated_since}, ensure_ascii=False, sort_keys=True))
                updated_since = started_at
                last_backup_at = now
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                ok = False
                print(json.dumps({"task": "backup", "ok": False, "error": type(exc).__name__}, ensure_ascii=False, sort_keys=True))
        if once or now - last_cleanup_at >= cleanup_interval_seconds:
            try:
                payload = post_retention_cleanup(base_url, token=token, tenant_id=tenant_id, retention_days=retention_days, timeout=timeout)
                print(json.dumps({"task": "retention_cleanup", "ok": True, "data": payload.get("data", {})}, ensure_ascii=False, sort_keys=True))
                last_cleanup_at = now
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                ok = False
                print(json.dumps({"task": "retention_cleanup", "ok": False, "error": type(exc).__name__}, ensure_ascii=False, sort_keys=True))
        if once:
            return 0 if ok else 1
        time.sleep(max(1.0, min(backup_interval_seconds, cleanup_interval_seconds, 60.0)))


def main() -> int:
    parser = argparse.ArgumentParser(description="周期性运行 PortraitHub 数据治理任务。")
    parser.add_argument("--base-url", default=os.getenv("PORTRAIT_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.getenv("PORTRAIT_GOVERNANCE_TOKEN"))
    parser.add_argument("--tenant-id", default=os.getenv("PORTRAIT_GOVERNANCE_TENANT", "default"))
    parser.add_argument("--backup-interval-seconds", type=float, default=3600.0)
    parser.add_argument("--cleanup-interval-seconds", type=float, default=86400.0)
    parser.add_argument("--retention-days", type=int, default=int_env("DATA_RETENTION_DAYS", 90))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run_governance_scheduler(
        base_url=args.base_url,
        token=args.token,
        tenant_id=args.tenant_id,
        backup_interval_seconds=args.backup_interval_seconds,
        cleanup_interval_seconds=args.cleanup_interval_seconds,
        retention_days=args.retention_days,
        once=args.once,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
