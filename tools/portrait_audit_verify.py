from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.portrait_audit import verify_audit_chain


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PortraitHub audit hash-chain integrity.")
    parser.add_argument("--path", type=Path, default=None, help="Audit JSONL path. Defaults to PORTRAIT_AUDIT_PATH.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    result = verify_audit_chain(args.path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    elif result["ok"]:
        print(f"OK: {result['record_count']} audit records verified; head={result['head_hash']}")
    else:
        print(f"FAILED: {result['error_count']} audit chain errors in {result['path']}")
        for error in result["errors"]:
            print(f"line {error['line']}: {error['reason']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
