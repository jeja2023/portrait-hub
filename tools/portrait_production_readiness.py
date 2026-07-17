"""PortraitHub 生产环境就绪度报告脚本（CLI 门面）。

检查实现位于 tools/readiness/ 包（按领域分组的 checks_* 模块）与
tools/readiness_checks.py（模型能力与构件检查）；本文件只负责聚合与输出，
并为既有调用方（CI、tests）保留 check_* 的导入路径。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.readiness import (  # noqa: E402  sys.path 注入后再导入
    check_data_stack,
    check_security_controls,
    check_templates,
)
from tools.readiness_checks import (  # noqa: E402  sys.path 注入后再导入
    check_capabilities,
    check_model_files,
)

__all__ = [
    "check_capabilities",
    "check_data_stack",
    "check_model_files",
    "check_security_controls",
    "check_templates",
    "main",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 PortraitHub 生产就绪状态。")
    parser.add_argument("--root", default=".")
    parser.add_argument("--models-root", default="models")
    parser.add_argument(
        "--scope",
        choices=["all", "platform"],
        default="all",
        help="使用 platform 跳过真实模型能力状态检查，同时保留构件和契约检查。",
    )
    parser.add_argument(
        "--strict", action="store_true", help="遇到回退能力或缺失模型文件时失败。"
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    models_root = Path(args.models_root).resolve()
    checks = [
        *check_templates(root),
        *check_data_stack(root),
        *check_security_controls(root),
    ]
    skipped: list[dict[str, Any]] = []
    if args.scope == "all":
        checks.extend(check_capabilities(root))
    else:
        skipped.append(
            {
                "name": "capabilities",
                "reason": "scope=platform skips real model capability status",
            }
        )
    checks.extend(check_model_files(root, models_root, skip_existence=(args.scope == "platform")))
    strict_failures = [item for item in checks if not item["ok"]]
    output = {
        "ok": not strict_failures if args.strict else True,
        "strict": args.strict,
        "scope": args.scope,
        "models_root": str(models_root),
        "checks": checks,
        "skipped": skipped,
        "strict_failure_count": len(strict_failures),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if args.strict and strict_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
