from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

PRODUCTION_PROFILES = {"private_standard", "private_ha", "platform_api"}
REPORT_KINDS = {"capacity_report", "recovery_drill"}
CAPACITY_SCENARIOS = {
    "single_image_sync",
    "image_batch",
    "vector_extract_compare_search",
    "gallery_ingest_rebuild_query",
    "video_upload_async",
    "stream_processing",
    "multi_tenant_burst",
}
CAPACITY_TOPOLOGIES = {"single_node", "dual_node", "target_cluster"}
CAPACITY_BREAKDOWNS = {"interface", "model", "tenant", "project"}


class AcceptanceEvidenceError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceEvidenceError(f"report cannot be read: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise AcceptanceEvidenceError("report root must be an object")
    return payload


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC) <= datetime.now(UTC)


def _validate_identity(payload: dict[str, Any], expected_kind: str) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if payload.get("kind") != expected_kind:
        errors.append(f"kind must be {expected_kind}")
    if payload.get("profile") not in PRODUCTION_PROFILES:
        errors.append("profile must be a production delivery profile")
    if not isinstance(payload.get("environment_id"), str) or not payload["environment_id"].strip():
        errors.append("environment_id is required")
    if not _valid_timestamp(payload.get("completed_at")):
        errors.append("completed_at must be a timezone-aware timestamp that is not in the future")
    if not isinstance(payload.get("git_commit"), str) or len(payload["git_commit"]) < 7:
        errors.append("git_commit is required")
    image_digest = str(payload.get("image_digest") or "")
    if not image_digest.startswith("sha256:") or len(image_digest) != 71:
        errors.append("image_digest must be a sha256 OCI digest")
    return errors


def _validate_sources(payload: dict[str, Any], *, base_dir: Path, verify_sources: bool) -> list[str]:
    errors: list[str] = []
    sources = payload.get("raw_evidence")
    if not isinstance(sources, list) or not sources:
        return ["raw_evidence must include at least one source artifact"]
    seen: set[str] = set()
    for index, source in enumerate(sources):
        prefix = f"raw_evidence[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{prefix} must be an object")
            continue
        source_path = str(source.get("path") or "")
        expected_hash = str(source.get("sha256") or "")
        if not source_path or len(expected_hash) != 64:
            errors.append(f"{prefix} requires path and sha256")
            continue
        if source_path in seen:
            errors.append(f"{prefix} duplicates source path {source_path}")
        seen.add(source_path)
        if verify_sources:
            path = (base_dir / source_path).resolve() if not Path(source_path).is_absolute() else Path(source_path).resolve()
            if not path.is_file():
                errors.append(f"{prefix} source does not exist")
            elif file_sha256(path) != expected_hash:
                errors.append(f"{prefix} sha256 does not match")
    return errors


def _validate_approvals(payload: dict[str, Any], required_roles: set[str]) -> list[str]:
    approvals = payload.get("approvals")
    if not isinstance(approvals, list):
        return ["approvals must be an array"]
    approved_roles: set[str] = set()
    errors: list[str] = []
    for index, approval in enumerate(approvals):
        if not isinstance(approval, dict):
            errors.append(f"approvals[{index}] must be an object")
            continue
        if approval.get("decision") != "approved":
            continue
        role = str(approval.get("role") or "")
        actor = str(approval.get("actor") or "")
        if not role or not actor or not _valid_timestamp(approval.get("at")):
            errors.append(f"approvals[{index}] is incomplete")
            continue
        approved_roles.add(role)
    missing = sorted(required_roles - approved_roles)
    if missing:
        errors.append("missing required approvals: " + ", ".join(missing))
    return errors


def _numeric(value: Any, *, minimum: float | None = None, maximum: float | None = None) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _validate_latency(prefix: str, latency: Any) -> list[str]:
    if not isinstance(latency, dict):
        return [f"{prefix} must be an object"]
    values = [latency.get(percentile) for percentile in ("p50", "p95", "p99")]
    if not all(_numeric(value, minimum=0) for value in values):
        return [f"{prefix} must contain non-negative numeric p50, p95 and p99"]
    numeric_values = [float(cast(int | float, value)) for value in values]
    if not numeric_values[0] <= numeric_values[1] <= numeric_values[2]:
        return [f"{prefix} must satisfy p50 <= p95 <= p99"]
    return []


def _validate_capacity_scenarios(payload: dict[str, Any]) -> list[str]:
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        return ["scenarios must be an array"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, scenario in enumerate(scenarios):
        prefix = f"scenarios[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{prefix} must be an object")
            continue
        scenario_id = str(scenario.get("id") or "")
        if scenario_id not in CAPACITY_SCENARIOS:
            errors.append(f"{prefix}.id is not a required capacity scenario")
        elif scenario_id in seen:
            errors.append(f"{prefix}.id duplicates {scenario_id}")
        seen.add(scenario_id)
        if scenario.get("status") != "passed":
            errors.append(f"{prefix}.status must be passed")
        if not _numeric(scenario.get("throughput_per_second"), minimum=0.000001):
            errors.append(f"{prefix}.throughput_per_second must be positive")
        if not _numeric(scenario.get("success_rate"), minimum=0, maximum=1):
            errors.append(f"{prefix}.success_rate must be within 0..1")
        if not _numeric(scenario.get("error_rate"), minimum=0, maximum=1):
            errors.append(f"{prefix}.error_rate must be within 0..1")
        errors.extend(_validate_latency(f"{prefix}.latency_ms", scenario.get("latency_ms")))
        stages = scenario.get("stage_latency_ms")
        if not isinstance(stages, dict):
            errors.append(f"{prefix}.stage_latency_ms must be an object")
        else:
            for stage in ("queue", "preprocess", "execution", "postprocess"):
                if not _numeric(stages.get(stage), minimum=0):
                    errors.append(f"{prefix}.stage_latency_ms.{stage} must be non-negative")
    missing = sorted(CAPACITY_SCENARIOS - seen)
    if missing:
        errors.append("missing required capacity scenarios: " + ", ".join(missing))
    return errors


def _validate_capacity_resources(payload: dict[str, Any]) -> list[str]:
    resources = payload.get("resource_observations")
    if not isinstance(resources, dict):
        return ["resource_observations must be an object"]
    errors: list[str] = []
    bounded_ratios = (
        "gpu_utilization_peak_ratio",
        "gpu_memory_fragmentation_peak_ratio",
        "cpu_peak_ratio",
    )
    for name in bounded_ratios:
        if not _numeric(resources.get(name), minimum=0, maximum=1):
            errors.append(f"resource_observations.{name} must be within 0..1")
    positive_or_zero = (
        "gpu_memory_peak_bytes",
        "memory_peak_bytes",
        "network_peak_bytes_per_second",
        "disk_peak_bytes_per_second",
        "object_storage_peak_bytes_per_second",
        "worker_concurrency_peak",
        "batch_size_peak",
        "queue_depth_peak",
        "backlog_p95_seconds",
    )
    for name in positive_or_zero:
        if not _numeric(resources.get(name), minimum=0):
            errors.append(f"resource_observations.{name} must be non-negative")
    if resources.get("gpu_oom_count") != 0:
        errors.append("resource_observations.gpu_oom_count must be 0")
    return errors


def _validate_vector_search(payload: dict[str, Any]) -> list[str]:
    vector = payload.get("vector_search")
    if not isinstance(vector, dict):
        return ["vector_search must be an object"]
    errors = _validate_latency("vector_search.latency_ms", vector.get("latency_ms"))
    for name in ("index_size_bytes", "update_p95_ms"):
        if not _numeric(vector.get(name), minimum=0):
            errors.append(f"vector_search.{name} must be non-negative")
    if not _numeric(vector.get("recall_at_k"), minimum=0, maximum=1):
        errors.append("vector_search.recall_at_k must be within 0..1")
    return errors


def _validate_capacity_breakdowns(payload: dict[str, Any]) -> list[str]:
    breakdowns = payload.get("breakdowns")
    if not isinstance(breakdowns, dict):
        return ["breakdowns must be an object"]
    errors: list[str] = []
    for dimension in sorted(CAPACITY_BREAKDOWNS):
        rows = breakdowns.get(dimension)
        if not isinstance(rows, list) or not rows:
            errors.append(f"breakdowns.{dimension} must contain measured rows")
            continue
        for index, row in enumerate(rows):
            prefix = f"breakdowns.{dimension}[{index}]"
            if not isinstance(row, dict) or not str(row.get("key") or ""):
                errors.append(f"{prefix} requires key")
                continue
            if not _numeric(row.get("throughput_per_second"), minimum=0):
                errors.append(f"{prefix}.throughput_per_second must be non-negative")
            if not _numeric(row.get("error_rate"), minimum=0, maximum=1):
                errors.append(f"{prefix}.error_rate must be within 0..1")
    return errors


def _validate_capacity_table(payload: dict[str, Any]) -> list[str]:
    rows = payload.get("capacity_table")
    if not isinstance(rows, list):
        return ["capacity_table must be an array"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        prefix = f"capacity_table[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        topology = str(row.get("topology") or "")
        if topology not in CAPACITY_TOPOLOGIES:
            errors.append(f"{prefix}.topology is not supported")
        elif topology in seen:
            errors.append(f"{prefix}.topology duplicates {topology}")
        seen.add(topology)
        if not isinstance(row.get("hardware"), dict) or not row["hardware"]:
            errors.append(f"{prefix}.hardware is required")
        for name in ("rated_throughput_per_second", "safe_concurrency", "max_tested_load", "recovery_seconds"):
            if not _numeric(row.get(name), minimum=0):
                errors.append(f"{prefix}.{name} must be non-negative")
        if not _numeric(row.get("headroom_ratio"), minimum=0.2, maximum=1):
            errors.append(f"{prefix}.headroom_ratio must be within 0.2..1")
        overload = row.get("overload_behavior")
        if overload not in {"rate_limited", "queued_then_rate_limited"}:
            errors.append(f"{prefix}.overload_behavior must be observable rate limiting")
    missing = sorted(CAPACITY_TOPOLOGIES - seen)
    if missing:
        errors.append("missing required capacity topologies: " + ", ".join(missing))
    return errors


def _validate_capacity_recommendations(payload: dict[str, Any]) -> list[str]:
    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, dict):
        return ["recommendations must be an object"]
    errors: list[str] = []
    for name in ("scale_out", "rate_limit", "timeout", "retry", "circuit_breaker"):
        value = recommendations.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"recommendations.{name} is required")
    if not _numeric(recommendations.get("hardware_headroom_ratio"), minimum=0.2, maximum=1):
        errors.append("recommendations.hardware_headroom_ratio must be within 0.2..1")
    return errors


def validate_capacity_report(
    payload: dict[str, Any],
    *,
    base_dir: Path = Path("."),
    verify_sources: bool = True,
) -> dict[str, Any]:
    errors = _validate_identity(payload, "capacity_report")
    errors.extend(_validate_sources(payload, base_dir=base_dir, verify_sources=verify_sources))
    errors.extend(_validate_approvals(payload, {"platform_sre", "test_owner"}))
    test = payload.get("test")
    metrics = payload.get("metrics")
    if not isinstance(test, dict):
        errors.append("test must be an object")
        test = {}
    if not isinstance(metrics, dict):
        errors.append("metrics must be an object")
        metrics = {}
    if float(test.get("rated_duration_minutes") or 0) < 30:
        errors.append("rated load duration must be at least 30 minutes")
    if payload.get("profile") in {"private_ha", "platform_api"} and float(test.get("burst_duration_minutes") or 0) < 10:
        errors.append("HA/platform burst duration must be at least 10 minutes")
    if not isinstance(test.get("model_versions"), list) or not test["model_versions"]:
        errors.append("test.model_versions is required")
    if not isinstance(test.get("hardware"), dict) or not test["hardware"]:
        errors.append("test.hardware is required")
    fingerprint = str(test.get("configuration_fingerprint") or "")
    if len(fingerprint) != 64:
        errors.append("test.configuration_fingerprint must be a sha256 digest")
    dataset = test.get("dataset_manifest")
    if not isinstance(dataset, dict) or not str(dataset.get("version") or "") or len(str(dataset.get("sha256") or "")) != 64:
        errors.append("test.dataset_manifest requires version and sha256")
    statistics = test.get("statistics")
    if not isinstance(statistics, dict):
        errors.append("test.statistics must be an object")
    else:
        if statistics.get("percentile_method") not in {"nearest_rank", "hdr_histogram", "tdigest"}:
            errors.append("test.statistics.percentile_method is not supported")
        if not _numeric(statistics.get("warmup_minutes"), minimum=0):
            errors.append("test.statistics.warmup_minutes must be non-negative")
        if not _numeric(statistics.get("sample_count"), minimum=1):
            errors.append("test.statistics.sample_count must be positive")
    errors.extend(_validate_capacity_scenarios(payload))
    errors.extend(_validate_capacity_resources(payload))
    errors.extend(_validate_vector_search(payload))
    errors.extend(_validate_capacity_breakdowns(payload))
    errors.extend(_validate_capacity_table(payload))
    errors.extend(_validate_capacity_recommendations(payload))
    thresholds: list[tuple[str, str, float]] = [
        ("inference_p95_seconds", "lt", 2.0),
        ("gpu_queue_p95_ms", "lt", 500.0),
        ("system_error_rate", "lt", 0.01),
        ("queue_growth_at_end", "le", 0.0),
        ("model_quality_drop_percentage_points", "le", 1.0),
        ("critical_vulnerabilities", "le", 0.0),
        ("high_vulnerabilities", "le", 0.0),
    ]
    threshold_results: list[dict[str, Any]] = []
    for name, operator, limit in thresholds:
        value = metrics.get(name)
        if not isinstance(value, (int, float)):
            errors.append(f"metrics.{name} must be numeric")
            continue
        passed = value < limit if operator == "lt" else value <= limit
        threshold_results.append({"metric": name, "value": value, "operator": operator, "limit": limit, "passed": passed})
        if not passed:
            errors.append(f"metrics.{name} failed threshold {operator} {limit}")
    if metrics.get("batching_enabled") is True:
        gain = metrics.get("batch_throughput_gain")
        regression = metrics.get("batch_p95_regression")
        if not isinstance(gain, (int, float)) or gain < 0.20:
            errors.append("batch throughput gain must be at least 20% when batching is enabled")
        if not isinstance(regression, (int, float)) or regression > 0.10:
            errors.append("batch p95 regression must not exceed 10% when batching is enabled")
    return {
        "kind": "capacity_report",
        "ok": not errors,
        "profile": payload.get("profile"),
        "environment_id": payload.get("environment_id"),
        "threshold_results": threshold_results,
        "errors": errors,
    }


def validate_recovery_report(
    payload: dict[str, Any],
    *,
    base_dir: Path = Path("."),
    verify_sources: bool = True,
) -> dict[str, Any]:
    errors = _validate_identity(payload, "recovery_drill")
    errors.extend(_validate_sources(payload, base_dir=base_dir, verify_sources=verify_sources))
    errors.extend(_validate_approvals(payload, {"platform_sre", "data_owner"}))
    measurements = payload.get("measurements")
    reconciliation = payload.get("reconciliation")
    if not isinstance(measurements, dict):
        errors.append("measurements must be an object")
        measurements = {}
    if not isinstance(reconciliation, dict):
        errors.append("reconciliation must be an object")
        reconciliation = {}
    profile = payload.get("profile")
    rto_limit = 240.0 if profile == "private_standard" else 60.0
    rpo_limit = 1440.0 if profile == "private_standard" else 15.0
    measured_rto = measurements.get("rto_minutes")
    measured_rpo = measurements.get("rpo_minutes")
    if not isinstance(measured_rto, (int, float)) or measured_rto < 0 or measured_rto > rto_limit:
        errors.append(f"measurements.rto_minutes must be within 0..{rto_limit}")
    if not isinstance(measured_rpo, (int, float)) or measured_rpo < 0 or measured_rpo > rpo_limit:
        errors.append(f"measurements.rpo_minutes must be within 0..{rpo_limit}")
    if reconciliation.get("lost_confirmed_writes") != 0:
        errors.append("reconciliation.lost_confirmed_writes must be 0")
    if reconciliation.get("duplicate_delivery_converged") is not True:
        errors.append("duplicate deliveries must converge under the idempotency contract")
    if reconciliation.get("database_count_match") is not True:
        errors.append("database row counts must reconcile")
    if reconciliation.get("object_digest_match") is not True:
        errors.append("object digests must reconcile")
    if reconciliation.get("vector_count_match") is not True:
        errors.append("vector counts must reconcile")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("scenarios must list the exercised failures")
    return {
        "kind": "recovery_drill",
        "ok": not errors,
        "profile": profile,
        "environment_id": payload.get("environment_id"),
        "thresholds": {"rto_minutes": rto_limit, "rpo_minutes": rpo_limit},
        "errors": errors,
    }


def validate_report(
    payload: dict[str, Any],
    *,
    base_dir: Path = Path("."),
    verify_sources: bool = True,
) -> dict[str, Any]:
    kind = payload.get("kind")
    if kind == "capacity_report":
        return validate_capacity_report(payload, base_dir=base_dir, verify_sources=verify_sources)
    if kind == "recovery_drill":
        return validate_recovery_report(payload, base_dir=base_dir, verify_sources=verify_sources)
    return {"kind": kind, "ok": False, "errors": [f"unsupported report kind: {kind}"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate measured capacity and recovery evidence against release gates.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--no-verify-sources", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = load_report(args.report)
        result = validate_report(
            payload,
            base_dir=args.report.resolve().parent,
            verify_sources=not args.no_verify_sources,
        )
    except AcceptanceEvidenceError as exc:
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
