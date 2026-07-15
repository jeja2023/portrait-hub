from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sdk.python.portrait_hub_client import PortraitHubClient


DEFAULT_BASE_URL = "http://127.0.0.1:9001"


def build_client() -> PortraitHubClient:
    return PortraitHubClient(
        base_url=os.getenv("PORTRAIT_HUB_BASE_URL", DEFAULT_BASE_URL),
        tenant_id=os.getenv("PORTRAIT_HUB_TENANT_ID", "tenant-a"),
        api_token=os.getenv("PORTRAIT_HUB_API_TOKEN"),
        auth_scheme=os.getenv("PORTRAIT_HUB_AUTH_SCHEME", "api_key"),
        timeout=float(os.getenv("PORTRAIT_HUB_TIMEOUT", "30")),
    )


def summarize(label: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    return {
        "step": label,
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "request_id": payload.get("request_id") if isinstance(payload, dict) else None,
        "data_keys": sorted(data) if isinstance(data, dict) else [],
    }


def run_demo(args: argparse.Namespace) -> dict[str, Any]:
    client = build_client()
    steps: list[dict[str, Any]] = []
    planned = ["health", "models", "thresholds"]
    if args.image:
        planned += ["enroll", "search"]
    if args.image and args.image_b:
        planned.append("compare_persons")
    if args.video:
        planned.append("create_video_job")
    if args.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "base_url": client.base_url,
            "tenant_id": client.tenant_id,
            "auth_scheme": client.auth_scheme,
            "planned_steps": planned,
        }

    steps.append(summarize("health", client.health()))
    steps.append(summarize("models", client.models()))
    steps.append(summarize("thresholds", client.thresholds()))

    if args.image:
        image = Path(args.image)
        steps.append(summarize("enroll", client.enroll(args.person_id, [image], modality="body")))
        steps.append(summarize("search", client.search(image, modality="body", top_k=args.top_k)))
    if args.image and args.image_b:
        steps.append(summarize("compare_persons", client.compare_persons(Path(args.image), Path(args.image_b), args.threshold_profile)))
    if args.video:
        steps.append(summarize("create_video_job", client.create_video_job(
            Path(args.video),
            sample_interval_seconds=args.sample_interval_seconds,
            batch_size=args.batch_size,
        )))

    return {"ok": True, "dry_run": False, "steps": steps}


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Python PortraitHub 演示客户端。")
    parser.add_argument("--image", help="用于注册/检索的身体图片，也可作为比对 A 图。")
    parser.add_argument("--image-b", help="compare_persons 使用的第二张图片。")
    parser.add_argument("--video", help="create_video_job 使用的视频文件。")
    parser.add_argument("--person-id", default="demo-python-person")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold-profile", default="normal")
    parser.add_argument("--sample-interval-seconds", type=float, default=1.0)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_demo(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
