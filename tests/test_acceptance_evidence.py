from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime

from tools.portrait_acceptance_evidence import validate_capacity_report, validate_recovery_report


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def source(tmp_path):
    path = tmp_path / "raw.json"
    path.write_text('{"measured":true}', encoding="utf-8")
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def approvals(*roles: str) -> list[dict[str, str]]:
    return [{"role": role, "actor": f"{role}-owner", "decision": "approved", "at": timestamp()} for role in roles]


def identity(kind: str, profile: str, raw_source: dict[str, str]) -> dict:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "profile": profile,
        "environment_id": "staging-commercial-a",
        "completed_at": timestamp(),
        "git_commit": "0123456789abcdef",
        "image_digest": "sha256:" + "a" * 64,
        "raw_evidence": [raw_source],
    }


def capacity_measurements() -> dict:
    scenario_ids = (
        "single_image_sync",
        "image_batch",
        "vector_extract_compare_search",
        "gallery_ingest_rebuild_query",
        "video_upload_async",
        "stream_processing",
        "multi_tenant_burst",
    )
    scenarios = [
        {
            "id": scenario_id,
            "status": "passed",
            "throughput_per_second": 10,
            "success_rate": 0.999,
            "error_rate": 0.001,
            "latency_ms": {"p50": 100, "p95": 500, "p99": 900},
            "stage_latency_ms": {"queue": 10, "preprocess": 20, "execution": 60, "postprocess": 10},
        }
        for scenario_id in scenario_ids
    ]
    breakdown_row = {"key": "measured", "throughput_per_second": 10, "error_rate": 0.001}
    capacity_table = [
        {
            "topology": topology,
            "hardware": {"gpu": "NVIDIA L4", "count": 2},
            "rated_throughput_per_second": 10,
            "safe_concurrency": 8,
            "max_tested_load": 15,
            "headroom_ratio": 0.25,
            "overload_behavior": "queued_then_rate_limited",
            "recovery_seconds": 10,
        }
        for topology in ("single_node", "dual_node", "target_cluster")
    ]
    return {
        "scenarios": scenarios,
        "resource_observations": {
            "gpu_utilization_peak_ratio": 0.8,
            "gpu_memory_peak_bytes": 8_000_000_000,
            "gpu_memory_fragmentation_peak_ratio": 0.1,
            "gpu_oom_count": 0,
            "cpu_peak_ratio": 0.7,
            "memory_peak_bytes": 16_000_000_000,
            "network_peak_bytes_per_second": 100_000_000,
            "disk_peak_bytes_per_second": 50_000_000,
            "object_storage_peak_bytes_per_second": 25_000_000,
            "worker_concurrency_peak": 8,
            "batch_size_peak": 16,
            "queue_depth_peak": 20,
            "backlog_p95_seconds": 0.2,
        },
        "vector_search": {
            "latency_ms": {"p50": 5, "p95": 10, "p99": 15},
            "recall_at_k": 0.99,
            "index_size_bytes": 1_000_000,
            "update_p95_ms": 20,
        },
        "breakdowns": {
            dimension: [{**breakdown_row, "key": dimension}] for dimension in ("interface", "model", "tenant", "project")
        },
        "capacity_table": capacity_table,
        "recommendations": {
            "scale_out": "scale at sustained queue pressure",
            "rate_limit": "reject beyond the rated envelope",
            "timeout": "use the measured p99 plus margin",
            "retry": "retry transient failures with bounded backoff",
            "circuit_breaker": "open on sustained dependency errors",
            "hardware_headroom_ratio": 0.25,
        },
    }


def test_capacity_report_passes_thresholds_with_sources_and_approvals(tmp_path) -> None:
    report = {
        **identity("capacity_report", "private_ha", source(tmp_path)),
        "approvals": approvals("platform_sre", "test_owner"),
        "test": {
            "rated_duration_minutes": 30,
            "burst_duration_minutes": 10,
            "model_versions": ["osnet-x1-1@sha256:abc"],
            "hardware": {"gpu": "NVIDIA L4", "count": 2},
            "configuration_fingerprint": "b" * 64,
            "dataset_manifest": {"version": "capacity-v1", "sha256": "c" * 64},
            "statistics": {"percentile_method": "hdr_histogram", "warmup_minutes": 5, "sample_count": 1000},
        },
        **capacity_measurements(),
        "metrics": {
            "inference_p95_seconds": 1.2,
            "gpu_queue_p95_ms": 100,
            "system_error_rate": 0.001,
            "queue_growth_at_end": 0,
            "model_quality_drop_percentage_points": 0.2,
            "critical_vulnerabilities": 0,
            "high_vulnerabilities": 0,
            "batching_enabled": True,
            "batch_throughput_gain": 0.25,
            "batch_p95_regression": 0.05,
        },
    }

    result = validate_capacity_report(report, base_dir=tmp_path)

    assert result["ok"] is True
    assert all(item["passed"] for item in result["threshold_results"])


def test_capacity_report_rejects_missing_scenarios_topologies_and_reproducibility(tmp_path) -> None:
    report = {
        **identity("capacity_report", "private_standard", source(tmp_path)),
        "approvals": approvals("platform_sre", "test_owner"),
        "test": {
            "rated_duration_minutes": 30,
            "model_versions": ["model@sha256:abc"],
            "hardware": {"gpu": "NVIDIA L4"},
        },
        "metrics": {
            "inference_p95_seconds": 1,
            "gpu_queue_p95_ms": 100,
            "system_error_rate": 0,
            "queue_growth_at_end": 0,
            "model_quality_drop_percentage_points": 0,
            "critical_vulnerabilities": 0,
            "high_vulnerabilities": 0,
            "batching_enabled": False,
        },
    }

    result = validate_capacity_report(report, base_dir=tmp_path)

    assert result["ok"] is False
    assert any("configuration_fingerprint" in error for error in result["errors"])
    assert "scenarios must be an array" in result["errors"]
    assert "capacity_table must be an array" in result["errors"]


def test_capacity_report_rejects_failed_or_untraceable_measurements(tmp_path) -> None:
    raw = source(tmp_path)
    report = {
        **identity("capacity_report", "private_standard", raw),
        "approvals": approvals("platform_sre"),
        "test": {"rated_duration_minutes": 5, "model_versions": [], "hardware": {}},
        "metrics": {
            "inference_p95_seconds": 2.5,
            "gpu_queue_p95_ms": 500,
            "system_error_rate": 0.01,
            "queue_growth_at_end": 1,
            "model_quality_drop_percentage_points": 1.1,
            "critical_vulnerabilities": 1,
            "high_vulnerabilities": 0,
            "batching_enabled": False,
        },
    }
    report["raw_evidence"][0]["sha256"] = "0" * 64

    result = validate_capacity_report(report, base_dir=tmp_path)

    assert result["ok"] is False
    assert any("sha256" in error for error in result["errors"])
    assert any("test_owner" in error for error in result["errors"])


def test_recovery_report_enforces_rto_rpo_and_reconciliation(tmp_path) -> None:
    report = {
        **identity("recovery_drill", "private_ha", source(tmp_path)),
        "approvals": approvals("platform_sre", "data_owner"),
        "measurements": {"rto_minutes": 45, "rpo_minutes": 10},
        "reconciliation": {
            "lost_confirmed_writes": 0,
            "duplicate_delivery_converged": True,
            "database_count_match": True,
            "object_digest_match": True,
            "vector_count_match": True,
        },
        "scenarios": ["gpu-worker-node-loss", "postgres-primary-failover", "object-restore"],
    }

    assert validate_recovery_report(report, base_dir=tmp_path)["ok"] is True

    failed = copy.deepcopy(report)
    failed["measurements"] = {"rto_minutes": 61, "rpo_minutes": 16}
    failed["reconciliation"]["lost_confirmed_writes"] = 1
    result = validate_recovery_report(failed, base_dir=tmp_path)
    assert result["ok"] is False
    assert any("rto_minutes" in error for error in result["errors"])
    assert any("lost_confirmed_writes" in error for error in result["errors"])
