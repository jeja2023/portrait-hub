from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any


def request_once(url: str, *, method: str, token: str | None, tenant_id: str, timeout: float) -> dict[str, Any]:
    headers = {"x-tenant-id": tenant_id}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, method=method, headers=headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        status = f"error:{type(exc).__name__}"
    elapsed = time.perf_counter() - started
    return {"status": status, "seconds": elapsed}


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((percentile_value / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def run_load_test(
    *,
    url: str,
    method: str,
    requests: int,
    concurrency: int,
    token: str | None,
    tenant_id: str,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = [
            executor.submit(request_once, url, method=method, token=token, tenant_id=tenant_id, timeout=timeout)
            for _ in range(max(1, requests))
        ]
        for future in as_completed(futures):
            results.append(future.result())
    total_seconds = time.perf_counter() - started
    latencies = [float(item["seconds"]) for item in results]
    status_counts: dict[str, int] = {}
    for item in results:
        key = str(item["status"])
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "url": url,
        "method": method,
        "requests": len(results),
        "concurrency": concurrency,
        "total_seconds": round(total_seconds, 6),
        "requests_per_second": round(len(results) / max(total_seconds, 1e-9), 6),
        "latency_seconds": {
            "min": round(min(latencies), 6),
            "mean": round(statistics.fmean(latencies), 6),
            "p50": round(percentile(latencies, 50), 6),
            "p95": round(percentile(latencies, 95), 6),
            "max": round(max(latencies), 6),
        },
        "status_counts": status_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple PortraitHub HTTP load test.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--token", default=None)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    report = run_load_test(
        url=args.url,
        method=args.method.upper(),
        requests=args.requests,
        concurrency=args.concurrency,
        token=args.token,
        tenant_id=args.tenant_id,
        timeout=args.timeout,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
