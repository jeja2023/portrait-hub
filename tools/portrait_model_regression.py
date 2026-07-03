from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.portrait_algorithm_eval import load_manifest, run_evaluation  # noqa: E402


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


def evaluate_delta_gates(
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
    gates: list[dict[str, Any]],
    *,
    label: str,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        metric_path = str(gate.get("path") or "").strip()
        if not metric_path:
            continue
        baseline_value = value_at_path(baseline_report, metric_path)
        candidate_value = value_at_path(candidate_report, metric_path)
        if baseline_value is None or candidate_value is None:
            failures.append({"path": metric_path, "reason": "metric_missing", "label": label})
            continue
        try:
            baseline_float = float(baseline_value)
            candidate_float = float(candidate_value)
        except (TypeError, ValueError):
            failures.append({"path": metric_path, "reason": "metric_not_numeric", "label": label})
            continue
        delta = candidate_float - baseline_float
        if gate.get("min_delta") is not None and delta < float(gate["min_delta"]):
            failures.append({"path": metric_path, "reason": "delta_below_min", "label": label, "delta": round(delta, 6), "min_delta": float(gate["min_delta"])})
        if gate.get("max_delta") is not None and delta > float(gate["max_delta"]):
            failures.append({"path": metric_path, "reason": "delta_above_max", "label": label, "delta": round(delta, 6), "max_delta": float(gate["max_delta"])})
    return failures


def run_manifest_section(section: dict[str, Any]) -> dict[str, Any]:
    report = run_evaluation(section)
    gates = section.get("gates", [])
    gate_failures = evaluate_gates(report, gates if isinstance(gates, list) else [])
    report["gate_failures"] = gate_failures
    report["ok"] = bool(report.get("ok")) and not gate_failures
    return report


def run_experiment(experiment: dict[str, Any]) -> dict[str, Any]:
    baseline = experiment.get("baseline") if isinstance(experiment.get("baseline"), dict) else {}
    candidate = experiment.get("candidate") if isinstance(experiment.get("candidate"), dict) else {}
    shadow = experiment.get("shadow") if isinstance(experiment.get("shadow"), dict) else None
    baseline_report = run_manifest_section(baseline)
    candidate_report = run_manifest_section(candidate)
    gates = experiment.get("delta_gates", experiment.get("gates", []))
    delta_failures = evaluate_delta_gates(
        baseline_report,
        candidate_report,
        gates if isinstance(gates, list) else [],
        label="candidate_vs_baseline",
    )
    shadow_report = run_manifest_section(shadow) if isinstance(shadow, dict) else None
    shadow_failures: list[dict[str, Any]] = []
    shadow_gates = experiment.get("shadow_delta_gates", [])
    if shadow_report is not None:
        shadow_failures = evaluate_delta_gates(
            candidate_report,
            shadow_report,
            shadow_gates if isinstance(shadow_gates, list) else [],
            label="shadow_vs_candidate",
        )
    ok = baseline_report["ok"] and candidate_report["ok"] and not delta_failures and not shadow_failures
    if shadow_report is not None:
        ok = ok and shadow_report["ok"]
    return {
        "name": str(experiment.get("name") or "experiment"),
        "ok": ok,
        "baseline": baseline_report,
        "candidate": candidate_report,
        "shadow": shadow_report,
        "delta_failures": delta_failures,
        "shadow_delta_failures": shadow_failures,
    }


def run_experiments(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    experiments = manifest.get("experiments", [])
    if not isinstance(experiments, list):
        return []
    return [run_experiment(experiment) for experiment in experiments if isinstance(experiment, dict)]


def run_model_regression(manifest: dict[str, Any]) -> dict[str, Any]:
    report = run_manifest_section(manifest)
    experiments = run_experiments(manifest)
    if experiments:
        report["experiments"] = experiments
        report["ok"] = (bool(report.get("ok")) or not report.get("metrics")) and all(item["ok"] for item in experiments)
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
