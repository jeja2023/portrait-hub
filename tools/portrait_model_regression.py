from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.portrait_algorithm_eval import load_manifest, run_evaluation


def value_at_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return None
    return current


def evaluate_gates(report: dict[str, Any], gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        metric_path = str(gate.get("path") or "").strip()
        if not metric_path:
            continue
        value = value_at_path(report, metric_path)
        if value is None:
            failures.append({"path": metric_path, "reason": "metric_missing"})
            continue
        try:
            value_float = float(value)
        except (TypeError, ValueError):
            failures.append({"path": metric_path, "reason": "metric_not_numeric", "value": value})
            continue
        if gate.get("min") is not None and value_float < float(gate["min"]):
            failures.append({"path": metric_path, "reason": "below_min", "value": value_float, "min": float(gate["min"])})
        if gate.get("max") is not None and value_float > float(gate["max"]):
            failures.append({"path": metric_path, "reason": "above_max", "value": value_float, "max": float(gate["max"])})
    return failures


def run_model_regression(manifest: dict[str, Any]) -> dict[str, Any]:
    report = run_evaluation(manifest)
    gates = manifest.get("gates", [])
    gate_failures = evaluate_gates(report, gates if isinstance(gates, list) else [])
    report["gate_failures"] = gate_failures
    report["ok"] = bool(report.get("ok")) and not gate_failures
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PortraitHub model regression metrics and threshold gates.")
    parser.add_argument("--manifest", required=True, help="Regression manifest YAML/JSON.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report = run_model_regression(load_manifest(Path(args.manifest).resolve()))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"portrait model regression: {'OK' if report['ok'] else 'FAILED'}")
        for failure in report.get("gate_failures", []):
            print(f"gate failure: {json.dumps(failure, ensure_ascii=False, sort_keys=True)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
