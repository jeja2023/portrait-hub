from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.portrait_evidence_package import (
    REQUIRED_ARTIFACT_KINDS,
    EvidencePackageError,
    build_evidence_package,
    verify_evidence_package,
)

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = "staging-a"
PROFILE = "private_standard"
IMAGE_DIGEST = "sha256:" + "a" * 64


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def generic(kind: str) -> dict:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "ok": True,
        "environment_id": ENVIRONMENT,
        "completed_at": timestamp(),
        "source_refs": [{"sha256": "b" * 64}],
    }


def capacity_measurements() -> dict:
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
        for scenario_id in (
            "single_image_sync",
            "image_batch",
            "vector_extract_compare_search",
            "gallery_ingest_rebuild_query",
            "video_upload_async",
            "stream_processing",
            "multi_tenant_burst",
        )
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
            dimension: [{"key": dimension, "throughput_per_second": 10, "error_rate": 0.001}]
            for dimension in ("interface", "model", "tenant", "project")
        },
        "capacity_table": [
            {
                "topology": topology,
                "hardware": {"gpu": "NVIDIA L4"},
                "rated_throughput_per_second": 10,
                "safe_concurrency": 8,
                "max_tested_load": 15,
                "headroom_ratio": 0.25,
                "overload_behavior": "rate_limited",
                "recovery_seconds": 10,
            }
            for topology in ("single_node", "dual_node", "target_cluster")
        ],
        "recommendations": {
            "scale_out": "scale at sustained queue pressure",
            "rate_limit": "reject beyond the rated envelope",
            "timeout": "use the measured p99 plus margin",
            "retry": "retry transient failures with bounded backoff",
            "circuit_breaker": "open on sustained dependency errors",
            "hardware_headroom_ratio": 0.25,
        },
    }


def signing_keys(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def artifact_set(tmp_path: Path) -> dict[str, Path]:
    raw = tmp_path / "raw-measurements.json"
    raw.write_text('{"measured":true}', encoding="utf-8")
    raw_ref = {"path": raw.name, "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}
    artifacts = {
        "system_inventory": write_json(tmp_path / "system.json", generic("system_inventory")),
        "sbom": write_json(
            tmp_path / "sbom.json",
            {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [{"name": "portrait-hub"}]},
        ),
        "vulnerability_scan": write_json(
            tmp_path / "vulnerability.json",
            {**generic("vulnerability_scan"), "critical_vulnerabilities": 0, "high_vulnerabilities": 0},
        ),
        "supply_chain": write_json(
            tmp_path / "supply-chain.json",
            {
                **generic("supply_chain"),
                "image_digest": IMAGE_DIGEST,
                "signature_verified": True,
                "sbom_attestation_verified": True,
            },
        ),
        "model_inventory": write_json(
            tmp_path / "models.json",
            {
                **generic("model_inventory"),
                "models": [
                    {
                        "model_id": "osnet-x1-1",
                        "capability": "appearance",
                        "version": "1.0.0",
                        "sha256": "c" * 64,
                        "license_name": "approved-license",
                        "source_ref": "controlled-source",
                        "model_card_ref": "model-card.json",
                        "governance_ref": "governance.json",
                        "redistribution_allowed": True,
                    }
                ],
            },
        ),
        "configuration_baseline": write_json(tmp_path / "config.json", generic("configuration_baseline")),
        "privacy_compliance": write_json(
            tmp_path / "privacy.json",
            {
                **generic("privacy_compliance"),
                "controls": [
                    {"control_id": f"COM-{index:03d}", "status": "approved"} for index in range(1, 13)
                ],
            },
        ),
        "audit_chain": write_json(tmp_path / "audit.json", generic("audit_chain")),
        "capacity_report": write_json(
            tmp_path / "capacity.json",
            {
                "schema_version": "1.0",
                "kind": "capacity_report",
                "profile": PROFILE,
                "environment_id": ENVIRONMENT,
                "completed_at": timestamp(),
                "git_commit": "0123456789abcdef",
                "image_digest": IMAGE_DIGEST,
                "raw_evidence": [raw_ref],
                "approvals": [
                    {"role": role, "actor": f"{role}-owner", "decision": "approved", "at": timestamp()}
                    for role in ("platform_sre", "test_owner")
                ],
                "test": {
                    "rated_duration_minutes": 30,
                    "burst_duration_minutes": 0,
                    "model_versions": ["osnet-x1-1@1.0.0"],
                    "hardware": {"gpu": "NVIDIA L4"},
                    "configuration_fingerprint": "b" * 64,
                    "dataset_manifest": {"version": "capacity-v1", "sha256": "c" * 64},
                    "statistics": {"percentile_method": "hdr_histogram", "warmup_minutes": 5, "sample_count": 1000},
                },
                **capacity_measurements(),
                "metrics": {
                    "inference_p95_seconds": 1.5,
                    "gpu_queue_p95_ms": 200,
                    "system_error_rate": 0.001,
                    "queue_growth_at_end": 0,
                    "model_quality_drop_percentage_points": 0.5,
                    "critical_vulnerabilities": 0,
                    "high_vulnerabilities": 0,
                    "batching_enabled": False,
                },
            },
        ),
        "recovery_drill": write_json(
            tmp_path / "recovery.json",
            {
                "schema_version": "1.0",
                "kind": "recovery_drill",
                "profile": PROFILE,
                "environment_id": ENVIRONMENT,
                "completed_at": timestamp(),
                "git_commit": "0123456789abcdef",
                "image_digest": IMAGE_DIGEST,
                "raw_evidence": [raw_ref],
                "approvals": [
                    {"role": role, "actor": f"{role}-owner", "decision": "approved", "at": timestamp()}
                    for role in ("platform_sre", "data_owner")
                ],
                "measurements": {"rto_minutes": 180, "rpo_minutes": 60},
                "reconciliation": {
                    "lost_confirmed_writes": 0,
                    "duplicate_delivery_converged": True,
                    "database_count_match": True,
                    "object_digest_match": True,
                    "vector_count_match": True,
                },
                "scenarios": ["database-restore", "worker-restart"],
            },
        ),
        "sla_report": write_json(
            tmp_path / "sla.json",
            {**generic("sla_report"), "source_complete": True, "met": True, "definition_version": "1.0"},
        ),
        "support_matrix": ROOT / "deploy" / "support-matrix.json",
    }
    assert set(artifacts) == REQUIRED_ARTIFACT_KINDS
    return artifacts


@pytest.mark.parametrize("audience", ["internal", "customer"])
def test_signed_evidence_package_builds_and_verifies(tmp_path: Path, audience: str) -> None:
    private_key, public_key = signing_keys(tmp_path)
    output = tmp_path / f"evidence-{audience}.zip"

    built = build_evidence_package(
        artifacts=artifact_set(tmp_path),
        output=output,
        private_key_path=private_key,
        audience=audience,
        environment_id=ENVIRONMENT,
        profile=PROFILE,
        tenant_id="tenant-a",
        project_id="project-a",
        git_commit="0123456789abcdef",
        image_digest=IMAGE_DIGEST,
        actor="release-manager",
    )
    verified = verify_evidence_package(
        output,
        public_key,
        expected_environment=ENVIRONMENT,
        expected_profile=PROFILE,
    )

    assert built["ok"] is True
    assert built["artifact_count"] == len(REQUIRED_ARTIFACT_KINDS)
    assert verified["ok"] is True
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert any(name.startswith("artifacts/") for name in names) is (audience == "internal")
        assert any(name.startswith("summaries/") for name in names) is (audience == "customer")


def test_evidence_package_rejects_incomplete_privacy_controls_without_output(tmp_path: Path) -> None:
    private_key, _ = signing_keys(tmp_path)
    artifacts = artifact_set(tmp_path)
    privacy = json.loads(artifacts["privacy_compliance"].read_text(encoding="utf-8"))
    privacy["controls"] = privacy["controls"][:-1]
    write_json(artifacts["privacy_compliance"], privacy)
    output = tmp_path / "invalid.zip"

    with pytest.raises(EvidencePackageError, match="COM-012"):
        build_evidence_package(
            artifacts=artifacts,
            output=output,
            private_key_path=private_key,
            audience="internal",
            environment_id=ENVIRONMENT,
            profile=PROFILE,
            tenant_id="tenant-a",
            project_id="project-a",
            git_commit="0123456789abcdef",
            image_digest=IMAGE_DIGEST,
            actor="release-manager",
        )
    assert output.exists() is False


def test_evidence_package_rejects_untrusted_key(tmp_path: Path) -> None:
    private_key, _ = signing_keys(tmp_path)
    _, wrong_public_key = signing_keys(tmp_path / "other")
    output = tmp_path / "evidence.zip"
    build_evidence_package(
        artifacts=artifact_set(tmp_path),
        output=output,
        private_key_path=private_key,
        audience="customer",
        environment_id=ENVIRONMENT,
        profile=PROFILE,
        tenant_id="tenant-a",
        project_id="project-a",
        git_commit="0123456789abcdef",
        image_digest=IMAGE_DIGEST,
        actor="release-manager",
    )

    result = verify_evidence_package(output, wrong_public_key)

    assert result["ok"] is False
    assert any("trusted public key" in error or "signature" in error for error in result["errors"])
