from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.portrait_audit import verify_audit_chain


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 PortraitHub 审计哈希链完整性。")
    parser.add_argument("--path", type=Path, default=None, help="审计 JSONL 路径。默认使用 PORTRAIT_AUDIT_PATH。")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    args = parser.parse_args()

    result = verify_audit_chain(args.path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    elif result["ok"]:
        print(f"通过：已校验 {result['record_count']} 条审计记录；头哈希={result['head_hash']}")
    else:
        print(f"失败：{result['error_count']} 个审计链错误，路径={result['path']}")
        for error in result["errors"]:
            print(f"第 {error['line']} 行：{error['reason']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
