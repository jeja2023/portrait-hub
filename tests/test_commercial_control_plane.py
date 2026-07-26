from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import portrait_audit, portrait_commercial, portrait_metering, settings
from app.portrait_access import clear_access_state
from app.portrait_call_logs import clear_call_logs, record_call_log
from app.portrait_idempotency import reset_idempotency_store
from app.server import app


@pytest.fixture
def commercial_client(workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        portrait_commercial,
        "PORTRAIT_COMMERCIAL_STATE_PATH",
        workspace_tmp_path / "commercial.json",
    )
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", workspace_tmp_path / "audit.jsonl")
    clear_access_state()
    clear_call_logs()
    portrait_metering.reset_metering_state()
    reset_idempotency_store()
    portrait_commercial.reset_commercial_state()
    return TestClient(app)


def headers(tenant_id: str = "tenant-a", project_id: str = "default") -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id, "X-Project-ID": project_id}


def data(response: Any) -> dict[str, Any]:
    assert response.status_code < 400, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["schema_version"] == "1.0"
    assert payload["request_id"]
    return payload["data"]


def approved_compliance_payload(control_id: str) -> dict[str, Any]:
    backends = ["postgresql", "vector_store", "object_storage", "cache", "exports", "backups"]
    control_data = {
        "COM-001": {
            "responsible_contact": "privacy-owner",
            "necessity_assessment": "approved",
            "recipient_categories": ["authorized-operators"],
        },
        "COM-002": {
            "notice_version": "notice-v1",
            "consent_scope": "project-and-purpose",
            "obtained_at": 1_700_000_000,
            "source": "signed-form",
            "proof_ref": "evidence/consent.json",
            "withdrawal_status": "not_withdrawn",
        },
        "COM-003": {
            "minor_policy": "not_applicable",
            "guardian_consent_status": "not_required",
            "guardian_verification_status": "not_required",
        },
        "COM-004": {
            "alternative_available": True,
            "alternative_process": "manual credential verification",
        },
        "COM-005": {
            "assessment_ref": "evidence/pipia.json",
            "assessment_version": "1.0",
            "review_due_at": 4_102_444_800,
        },
        "COM-006": {
            "allowed_regions": ["cn-east-1"],
            "transfer_policy": "approved_only",
            "export_requires_approval": True,
        },
        "COM-007": {
            "backend_retention": {backend: 30 for backend in backends},
            "deletion_workflow": "verified-six-backend-deletion",
            "backup_expiry_policy": "expire-after-30-days",
        },
        "COM-008": {
            "identity_verification_policy": "two-factor-subject-verification",
            "due_days": 30,
            "fulfillment_backends": backends,
        },
        "COM-009": {
            "collection_area": "controlled-entry",
            "signage_ref": "evidence/signage.jpg",
            "controller": "site-privacy-owner",
            "prohibited_areas": ["restroom", "changing-room", "private-residence"],
        },
        "COM-010": {
            "filing_threshold": 100_000,
            "current_count": 0,
            "warning_ratio": 0.8,
            "filing_status": "monitoring",
        },
        "COM-011": {
            "human_review_enabled": True,
            "appeal_process": "review-and-appeal-workflow",
            "decision_use": "assistive_only",
        },
        "COM-012": {
            "incident_process": "privacy-incident-runbook",
            "notification_decision_owner": "privacy-owner",
            "response_plan_ref": "evidence/privacy-incident-plan.json",
        },
    }[control_id]
    return {
        "status": "approved",
        "applicability": "required",
        "processing_purpose": "controlled access analytics",
        "legal_basis": "customer-approved processing record",
        "evidence_refs": [f"evidence/{control_id.lower()}.json"],
        "control_data": control_data,
        "approved_by": "privacy-owner",
    }


def test_commercial_profile_transition_and_entitlement_versions(commercial_client: TestClient) -> None:
    profile = data(
        commercial_client.get(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
        )
    )["commercial_profile"]
    assert profile["commercial_status"] == "trial"
    assert profile["version"] == 1

    rejected = commercial_client.patch(
        "/v1/access/projects/default/commercial-profile",
        headers=headers(),
        json={"commercial_status": "active", "reason": "contract signed"},
    )
    assert rejected.status_code == 422

    active = data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "commercial_status": "active",
                "reason": "contract signed",
                "approved_by": "commercial-owner",
                "expected_version": 1,
            },
        )
    )["commercial_profile"]
    assert active["commercial_status"] == "active"
    assert active["version"] == 2

    first = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection", "tracking"],
                "concurrency_limit": 8,
                "reason": "initial entitlement",
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]
    second = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "definition_version": "1.1",
                "allowed_capabilities": ["face_detection", "tracking", "appearance"],
                "concurrency_limit": 16,
                "reason": "approved capability upgrade",
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]
    assert first["version"] == 1
    assert second["version"] == 2
    assert second["supersedes"] == first["entitlement_id"]
    entitlements = data(commercial_client.get("/v1/access/entitlements", headers=headers()))["entitlements"]
    assert entitlements[0]["status"] == "active"
    assert entitlements[1]["status"] == "superseded"


def test_commercial_status_transition_respects_effective_time_and_can_be_cancelled(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(portrait_commercial, "now_seconds", lambda: clock["now"])
    initial = data(
        commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers())
    )["commercial_profile"]
    scheduled = data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "commercial_status": "active",
                "effective_at": 2_000,
                "reason": "contract starts next period",
                "approved_by": "commercial-owner",
                "expected_version": initial["version"],
            },
        )
    )["commercial_profile"]
    assert scheduled["commercial_status"] == "trial"
    assert scheduled["scheduled_transition"]["status"] == "pending"

    clock["now"] = 2_000.0
    activated = data(
        commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers())
    )["commercial_profile"]
    assert activated["commercial_status"] == "active"
    assert activated["scheduled_transition"] is None
    assert activated["last_scheduled_transition"]["status"] == "completed"
    assert activated["status_history"][-1]["from"] == "trial"

    suspended = data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "commercial_status": "suspended",
                "effective_at": 3_000,
                "reason": "planned maintenance",
                "approved_by": "commercial-owner",
                "expected_version": activated["version"],
            },
        )
    )["commercial_profile"]
    cancelled = data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "cancel_scheduled_transition": True,
                "reason": "maintenance window withdrawn",
                "approved_by": "commercial-owner",
                "expected_version": suspended["version"],
            },
        )
    )["commercial_profile"]
    clock["now"] = 3_001.0
    unchanged = data(
        commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers())
    )["commercial_profile"]
    assert cancelled["last_scheduled_transition"]["status"] == "cancelled"
    assert unchanged["commercial_status"] == "active"


def test_future_entitlement_preserves_current_until_atomic_activation(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(portrait_commercial, "now_seconds", lambda: clock["now"])
    first = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection"],
                "reason": "initial contract",
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]
    scheduled = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection", "tracking"],
                "starts_at": 2_000,
                "change_type": "renewal",
                "reason": "renewal effective next period",
                "expected_current_entitlement_id": first["entitlement_id"],
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]

    before = data(
        commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers())
    )["commercial_profile"]
    assert scheduled["status"] == "pending"
    assert scheduled["rollback_target_id"] == first["entitlement_id"]
    assert before["current_entitlement_id"] == first["entitlement_id"]
    assert before["entitlement"]["status"] == "active"

    stale = commercial_client.post(
        "/v1/access/entitlements",
        headers=headers(),
        json={
            "allowed_capabilities": ["pose"],
            "starts_at": 3_000,
            "reason": "stale proposal",
            "expected_current_entitlement_id": "entitlement_stale",
            "approved_by": "commercial-owner",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "entitlement_current_version_conflict"

    clock["now"] = 2_000.0
    after = data(
        commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers())
    )["commercial_profile"]
    versions = data(commercial_client.get("/v1/access/entitlements", headers=headers()))["entitlements"]
    by_id = {item["entitlement_id"]: item for item in versions}
    assert after["current_entitlement_id"] == scheduled["entitlement_id"]
    assert by_id[scheduled["entitlement_id"]]["status"] == "active"
    assert by_id[first["entitlement_id"]]["status"] == "superseded"


def test_entitlement_cancel_rollback_and_temporary_expansion_restore(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(portrait_commercial, "now_seconds", lambda: clock["now"])
    baseline = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection"],
                "concurrency_limit": 2,
                "reason": "baseline",
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]
    pending = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection"],
                "starts_at": 5_000,
                "reason": "planned renewal",
                "expected_current_entitlement_id": baseline["entitlement_id"],
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]
    cancelled = data(
        commercial_client.post(
            f"/v1/access/entitlements/{pending['entitlement_id']}/actions",
            headers=headers(),
            json={
                "action": "cancel",
                "reason": "contract amendment withdrawn",
                "approved_by": "commercial-owner",
                "expected_version": pending["record_version"],
                "expected_current_entitlement_id": baseline["entitlement_id"],
            },
        )
    )["entitlement"]
    assert cancelled["status"] == "revoked"

    temporary = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection"],
                "concurrency_limit": 20,
                "change_type": "temporary_expansion",
                "expires_at": 1_200,
                "reason": "approved event capacity",
                "rollback_target_id": baseline["entitlement_id"],
                "expected_current_entitlement_id": baseline["entitlement_id"],
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]
    assert temporary["status"] == "active"

    rolled_back = data(
        commercial_client.post(
            f"/v1/access/entitlements/{temporary['entitlement_id']}/actions",
            headers=headers(),
            json={
                "action": "rollback",
                "reason": "capacity event cancelled",
                "approved_by": "commercial-owner",
                "expected_version": temporary["record_version"],
                "expected_current_entitlement_id": temporary["entitlement_id"],
            },
        )
    )
    assert rolled_back["commercial_profile"]["current_entitlement_id"] == baseline["entitlement_id"]
    assert rolled_back["commercial_profile"]["entitlement"]["status"] == "active"

    clock["now"] = 1_100.0
    temporary_again = data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection"],
                "concurrency_limit": 30,
                "change_type": "temporary_expansion",
                "expires_at": 1_200,
                "reason": "approved short capacity window",
                "rollback_target_id": baseline["entitlement_id"],
                "expected_current_entitlement_id": baseline["entitlement_id"],
                "approved_by": "commercial-owner",
            },
        )
    )["entitlement"]
    clock["now"] = 1_201.0
    restored = data(
        commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers())
    )["commercial_profile"]
    versions = data(commercial_client.get("/v1/access/entitlements", headers=headers()))["entitlements"]
    restored_temporary = next(item for item in versions if item["entitlement_id"] == temporary_again["entitlement_id"])
    assert restored["current_entitlement_id"] == baseline["entitlement_id"]
    assert restored_temporary["status"] == "expired"


def test_commercial_lists_support_cursor_search_sort_and_time_contract(commercial_client: TestClient) -> None:
    for index in range(3):
        data(
            commercial_client.post(
                "/v1/access/entitlements",
                headers=headers(),
                json={
                    "product_version": f"sku-{index}",
                    "allowed_capabilities": ["face_detection"],
                    "reason": f"entitlement version {index}",
                    "approved_by": "commercial-owner",
                },
            )
        )

    first = data(
        commercial_client.get(
            "/v1/access/entitlements",
            headers=headers(),
            params={"limit": 1, "sort_by": "version", "sort_order": "asc"},
        )
    )
    second = data(
        commercial_client.get(
            "/v1/access/entitlements",
            headers=headers(),
            params={"limit": 1, "sort_by": "version", "sort_order": "asc", "cursor": first["next_cursor"]},
        )
    )
    filtered = data(
        commercial_client.get(
            "/v1/access/entitlements",
            headers=headers(),
            params={"q": "sku-2"},
        )
    )

    assert first["items"] == first["entitlements"]
    assert first["count"] == 1
    assert first["total"] == 3
    assert first["has_more"] is True
    assert second["entitlements"][0]["version"] == 2
    assert filtered["total"] == 1
    assert filtered["entitlements"][0]["product_version"] == "sku-2"
    assert (
        commercial_client.get(
            "/v1/access/entitlements",
            headers=headers(),
            params={"cursor": "WzFd"},
        ).status_code
        == 422
    )


def test_control_write_idempotency_replays_and_rejects_conflicting_payloads(commercial_client: TestClient) -> None:
    request_headers = {**headers(), "Idempotency-Key": "entitlement-contract-20260724"}
    payload = {
        "product_version": "idempotent-sku",
        "allowed_capabilities": ["face_detection"],
        "reason": "idempotency contract",
        "approved_by": "commercial-owner",
    }

    first_response = commercial_client.post("/v1/access/entitlements", headers=request_headers, json=payload)
    replay_response = commercial_client.post("/v1/access/entitlements", headers=request_headers, json=payload)
    first = data(first_response)["entitlement"]
    replay = data(replay_response)["entitlement"]
    conflict = commercial_client.post(
        "/v1/access/entitlements",
        headers=request_headers,
        json={**payload, "product_version": "different-sku"},
    )
    listed = data(commercial_client.get("/v1/access/entitlements", headers=headers()))

    assert first_response.headers["Idempotency-Replayed"] == "false"
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert replay["entitlement_id"] == first["entitlement_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"
    assert listed["total"] == 1
    assert (
        commercial_client.get(
            "/v1/access/entitlements",
            headers=headers(),
            params={"created_since": 2, "created_until": 1},
        ).status_code
        == 422
    )


def test_platform_project_creation_enforces_tenant_project_limit(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED", True)
    monkeypatch.setattr(settings, "COMMERCIAL_LICENSE_REQUIRED", False)
    profile = data(commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers()))[
        "commercial_profile"
    ]
    data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "commercial_status": "active",
                "reason": "contract activated",
                "approved_by": "commercial-owner",
                "expected_version": profile["version"],
            },
        )
    )
    data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection"],
                "project_limit": 1,
                "reason": "project allocation plan",
                "approved_by": "commercial-owner",
            },
        )
    )

    rejected = commercial_client.post(
        "/v1/access/projects",
        headers=headers(),
        json={"project_id": "second-project", "name": "Second project"},
    )

    assert rejected.status_code == 403
    assert "project_limit_exceeded" in rejected.text


def test_offboarding_closure_requires_disabled_apps_and_deletion_evidence(commercial_client: TestClient) -> None:
    created_app = data(
        commercial_client.post(
            "/v1/access/applications",
            headers=headers(),
            json={"app_id": "offboarding-app", "name": "Offboarding app", "scopes": ["infer"]},
        )
    )["application"]
    profile = data(commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers()))[
        "commercial_profile"
    ]
    offboarding = data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "commercial_status": "offboarding",
                "reason": "contract terminated",
                "approved_by": "commercial-owner",
                "expected_version": profile["version"],
            },
        )
    )["commercial_profile"]

    blocked = commercial_client.patch(
        "/v1/access/projects/default/commercial-profile",
        headers=headers(),
        json={
            "commercial_status": "closed",
            "reason": "offboarding complete",
            "approved_by": "commercial-owner",
            "expected_version": offboarding["version"],
        },
    )
    assert blocked.status_code == 409
    assert "active_applications" in blocked.text
    assert "deletion_evidence_required" in blocked.text

    data(
        commercial_client.patch(
            f"/v1/access/applications/{created_app['app_id']}",
            headers=headers(),
            json={"status": "disabled"},
        )
    )
    rights_request = data(
        commercial_client.post(
            "/v1/admin/compliance/rights-requests",
            headers=headers(),
            json={"request_type": "deletion", "subject_reference": "project-offboarding"},
        )
    )["rights_request"]
    for next_status, identity in (
        ("identity_pending", "pending"),
        ("verified", "verified"),
        ("in_progress", "verified"),
    ):
        rights_request = data(
            commercial_client.patch(
                f"/v1/admin/compliance/rights-requests/{rights_request['rights_request_id']}",
                headers=headers(),
                json={
                    "status": next_status,
                    "identity_verification": identity,
                    "expected_version": rights_request["version"],
                },
            )
        )["rights_request"]
    evidence = [
        {"backend": backend, "status": "deleted", "evidence_sha256": "b" * 64}
        for backend in sorted(portrait_commercial.RIGHTS_EXECUTION_BACKENDS)
    ]
    data(
        commercial_client.patch(
            f"/v1/admin/compliance/rights-requests/{rights_request['rights_request_id']}",
            headers=headers(),
            json={
                "status": "completed",
                "identity_verification": "verified",
                "execution_evidence": evidence,
                "expected_version": rights_request["version"],
            },
        )
    )
    closed = data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "commercial_status": "closed",
                "reason": "offboarding complete",
                "approved_by": "commercial-owner",
                "expected_version": offboarding["version"],
            },
        )
    )["commercial_profile"]
    assert closed["commercial_status"] == "closed"


def test_usage_timeseries_quota_and_sla_are_recomputable(commercial_client: TestClient) -> None:
    record_call_log(
        request_id="req-success",
        tenant_id="tenant-a",
        project_id="default",
        method="POST",
        path="/v1/infer/persons",
        status_code=200,
        latency_ms=100,
        created_at=1_700_000_000,
        model_version="person-v1",
    )
    record_call_log(
        request_id="req-error",
        tenant_id="tenant-a",
        project_id="default",
        method="POST",
        path="/v1/infer/persons",
        status_code=503,
        latency_ms=300,
        created_at=1_700_000_010,
        model_version="person-v1",
        error_code="dependency_unavailable",
    )
    summary = data(commercial_client.get("/v1/access/usage/summary", headers=headers()))["usage_summary"]
    assert summary["request_count"] == 2
    assert summary["success_rate"] == 0.5
    assert summary["latency_ms"]["p50"] == 200
    points = data(commercial_client.get("/v1/access/usage/timeseries", headers=headers()))["timeseries"]
    assert points == [
        {
            "date": "2023-11-14",
            "request_count": 2,
            "success_count": 1,
            "error_count": 1,
            "success_rate": 0.5,
            "average_latency_ms": 200.0,
        }
    ]
    forecast = data(commercial_client.get("/v1/access/quota/forecast", headers=headers()))["quota_forecast"]
    assert forecast["project_id"] == "default"

    definition = data(
        commercial_client.post(
            "/v1/admin/operations/sla",
            headers=headers(),
            json={"definition_version": "2026-07", "availability_target": 0.995, "p95_latency_ms": 2000},
        )
    )["sla_definition"]
    report = data(
        commercial_client.post(
            "/v1/admin/operations/sla/reports",
            headers=headers(),
            json={"created_since": 1_699_999_000, "created_until": 1_700_001_000},
        )
    )["sla_report"]
    assert report["definition_version"] == definition["definition_version"]
    assert report["availability"] == 0.5
    assert report["met"] is False


def test_incident_timeline_cas_and_tenant_isolation(commercial_client: TestClient) -> None:
    incident = data(
        commercial_client.post(
            "/v1/admin/operations/incidents",
            headers=headers(),
            json={"title": "Object storage unavailable", "severity": "sev2", "impact_scope": "video jobs"},
        )
    )["incident"]
    assert incident["status"] == "investigating"
    updated = data(
        commercial_client.patch(
            f"/v1/admin/operations/incidents/{incident['incident_id']}",
            headers=headers(),
            json={"status": "resolved", "timeline_message": "storage recovered", "expected_version": 1},
        )
    )["incident"]
    assert updated["recovered_at"] is not None
    assert updated["version"] == 2
    conflict = commercial_client.patch(
        f"/v1/admin/operations/incidents/{incident['incident_id']}",
        headers=headers(),
        json={"status": "closed", "expected_version": 1},
    )
    assert conflict.status_code == 409

    other = data(commercial_client.get("/v1/admin/operations/incidents", headers=headers("tenant-b")))
    assert other["count"] == 0
    timeline = data(commercial_client.get("/v1/admin/operations/health-timeline", headers=headers()))
    assert timeline["count"] == 2


def test_compliance_rights_requests_and_template_fingerprint(commercial_client: TestClient) -> None:
    initial = data(commercial_client.get("/v1/admin/compliance/status", headers=headers()))["compliance"]
    assert initial["ready"] is False
    assert len(initial["blocking_controls"]) == 12

    incomplete = commercial_client.put(
        "/v1/admin/compliance/records/COM-001",
        headers=headers(),
        json={"status": "approved", "approved_by": "privacy-owner"},
    )
    assert incomplete.status_code == 422

    record = data(
        commercial_client.put(
            "/v1/admin/compliance/records/COM-001",
            headers=headers(),
            json=approved_compliance_payload("COM-001"),
        )
    )["compliance_record"]
    assert record["approved_by"] == "privacy-owner"
    current = data(commercial_client.get("/v1/admin/compliance/status", headers=headers()))["compliance"]
    assert "COM-001" not in current["blocking_controls"]
    assert len(current["blocking_controls"]) == 11

    rights_request = data(
        commercial_client.post(
            "/v1/admin/compliance/rights-requests",
            headers=headers(),
            json={"request_type": "deletion", "subject_reference": "customer-subject-42"},
        )
    )["rights_request"]
    assert rights_request["status"] == "received"


@pytest.mark.parametrize(
    ("control_id", "field", "unsafe_value", "violation_code"),
    [
        ("COM-003", "minor_policy", "allow_without_guardian", "minor_policy_invalid"),
        ("COM-004", "alternative_available", False, "alternative_not_available"),
        ("COM-006", "export_requires_approval", False, "export_approval_required"),
        ("COM-009", "collection_area", "restroom", "prohibited_area_selected"),
        ("COM-010", "warning_ratio", 0.95, "warning_ratio_invalid"),
        ("COM-011", "human_review_enabled", False, "human_review_required"),
        ("COM-011", "decision_use", "automatic_only", "sole_automated_decision_forbidden"),
        ("COM-012", "response_plan_ref", "", "privacy_incident_control_required"),
    ],
)
def test_compliance_controls_reject_unsafe_semantics(
    commercial_client: TestClient,
    control_id: str,
    field: str,
    unsafe_value: Any,
    violation_code: str,
) -> None:
    payload = approved_compliance_payload(control_id)
    payload["control_data"][field] = unsafe_value
    response = commercial_client.put(
        f"/v1/admin/compliance/records/{control_id}",
        headers=headers(),
        json=payload,
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "compliance_record_incomplete"
    assert violation_code in {item["code"] for item in error["details"]["semantic_violations"]}


def test_compliance_filing_warning_requires_workflow_and_is_reported(commercial_client: TestClient) -> None:
    rejected_payload = approved_compliance_payload("COM-010")
    rejected_payload["control_data"].update({"current_count": 80_000, "filing_status": "monitoring"})
    rejected = commercial_client.put(
        "/v1/admin/compliance/records/COM-010",
        headers=headers(),
        json=rejected_payload,
    )
    assert rejected.status_code == 422
    assert "filing_workflow_required" in rejected.text

    accepted_payload = approved_compliance_payload("COM-010")
    accepted_payload["control_data"].update(
        {"current_count": 80_000, "filing_status": "warning_acknowledged"}
    )
    record = data(
        commercial_client.put(
            "/v1/admin/compliance/records/COM-010",
            headers=headers(),
            json=accepted_payload,
        )
    )["compliance_record"]
    assert record["derived_status"]["warning"] is True
    assert record["derived_status"]["filing_required"] is False


def test_com006_blocks_all_export_surfaces_in_commercial_mode(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED", True)

    rights_response = commercial_client.post(
        "/v1/admin/compliance/rights-requests",
        headers=headers(),
        json={"request_type": "export", "subject_reference": "customer-subject-42"},
    )
    feedback_response = commercial_client.post(
        "/v1/evaluation/review-samples/export",
        headers=headers(),
        json={"sample_ids": ["missing-sample"]},
    )
    admin_response = commercial_client.get("/v1/admin/export", headers=headers())

    for response in (rights_response, feedback_response, admin_response):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "compliance_operation_blocked"
        assert response.json()["error"]["details"]["control_id"] == "COM-006"

    approved = approved_compliance_payload("COM-006")
    portrait_commercial.upsert_compliance_record(
        "tenant-a",
        "default",
        "COM-006",
        {key: value for key, value in approved.items() if key != "approved_by"},
        actor="privacy-owner",
        approved_by="privacy-owner",
    )

    assert commercial_client.get("/v1/admin/export", headers=headers()).status_code == 200
    assert (
        commercial_client.post(
            "/v1/admin/compliance/rights-requests",
            headers=headers(),
            json={"request_type": "export", "subject_reference": "customer-subject-42"},
        ).status_code
        == 200
    )
    assert (
        commercial_client.post(
            "/v1/evaluation/review-samples/export",
            headers=headers(),
            json={"sample_ids": ["missing-sample"]},
        ).status_code
        == 404
    )


def test_rights_request_lifecycle_and_template_fingerprint(commercial_client: TestClient) -> None:
    rights_request = data(
        commercial_client.post(
            "/v1/admin/compliance/rights-requests",
            headers=headers(),
            json={"request_type": "deletion", "subject_reference": "customer-subject-42"},
        )
    )["rights_request"]
    assert rights_request["subject_reference"] != "customer-subject-42"
    rights_id = rights_request["rights_request_id"]
    version = rights_request["version"]
    for next_status, identity in (
        ("identity_pending", "pending"),
        ("verified", "verified"),
        ("in_progress", "verified"),
    ):
        rights_request = data(
            commercial_client.patch(
                f"/v1/admin/compliance/rights-requests/{rights_id}",
                headers=headers(),
                json={
                    "status": next_status,
                    "identity_verification": identity,
                    "expected_version": version,
                },
            )
        )["rights_request"]
        version = rights_request["version"]
    incomplete_completion = commercial_client.patch(
        f"/v1/admin/compliance/rights-requests/{rights_id}",
        headers=headers(),
        json={"status": "completed", "identity_verification": "verified", "expected_version": version},
    )
    assert incomplete_completion.status_code == 422
    deletion_evidence = [
        {"backend": backend, "status": "deleted", "evidence_sha256": "a" * 64}
        for backend in ("postgresql", "vector_store", "object_storage", "cache", "exports", "backups")
    ]
    completed = data(
        commercial_client.patch(
            f"/v1/admin/compliance/rights-requests/{rights_id}",
            headers=headers(),
            json={
                "status": "completed",
                "identity_verification": "verified",
                "execution_evidence": deletion_evidence,
                "expected_version": version,
            },
        )
    )["rights_request"]
    assert completed["status"] == "completed"
    assert len(completed["execution_evidence"]) == 6

    templates = data(commercial_client.get("/v1/admin/industry-templates", headers=headers()))
    assert templates["count"] == 5
    template_id = templates["industry_templates"][0]["template_id"]
    preview = data(
        commercial_client.get(
            f"/v1/admin/industry-templates/{template_id}/preview",
            headers=headers(),
        )
    )["preview"]
    stale = commercial_client.post(
        f"/v1/admin/industry-templates/{template_id}/apply",
        headers=headers(),
        json={"expected_fingerprint": "0" * 64, "dry_run": False},
    )
    assert stale.status_code == 409
    applied = data(
        commercial_client.post(
            f"/v1/admin/industry-templates/{template_id}/apply",
            headers=headers(),
            json={"expected_fingerprint": preview["fingerprint"], "dry_run": False},
        )
    )["template_application"]
    assert applied["applied"] is True


def test_commercial_mutation_restores_state_when_audit_fails(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = data(
        commercial_client.get(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
        )
    )["commercial_profile"]

    def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.routes_portrait_commercial.audit_event", fail_audit)
    response = commercial_client.patch(
        "/v1/access/projects/default/commercial-profile",
        headers=headers(),
        json={"budget_limit": 1000, "expected_version": profile["version"]},
    )
    assert response.status_code == 500
    restored = portrait_commercial.get_commercial_profile("tenant-a", "default")
    assert restored["budget_limit"] is None
    assert restored["version"] == profile["version"]


def test_support_case_lifecycle_is_scoped_and_versioned(commercial_client: TestClient) -> None:
    created = data(
        commercial_client.post(
            "/v1/access/support/cases",
            headers=headers(),
            json={
                "title": "GPU worker repeatedly exits",
                "description": "Worker exits after model warmup; attachment contains redacted diagnostics only.",
                "severity": "sev2",
                "environment": "private-standard-a",
                "product_version": "0.18.0",
                "request_ids": ["req-1"],
                "task_ids": ["job-1"],
                "redacted_attachments": [{"object_ref": "support/redacted-1.txt", "sha256": "a" * 64}],
            },
        )
    )["support_case"]
    assert created["status"] == "open"
    assert created["version"] == 1

    updated = data(
        commercial_client.patch(
            f"/v1/access/support/cases/{created['support_case_id']}",
            headers=headers(),
            json={"status": "investigating", "owner": "sre-owner", "expected_version": 1},
        )
    )["support_case"]
    assert updated["status"] == "investigating"
    assert updated["version"] == 2
    conflict = commercial_client.patch(
        f"/v1/access/support/cases/{created['support_case_id']}",
        headers=headers(),
        json={"status": "resolved", "expected_version": 1},
    )
    assert conflict.status_code == 409
    tenant_b = data(commercial_client.get("/v1/access/support/cases", headers=headers("tenant-b")))
    assert tenant_b["count"] == 0


def test_industry_template_application_can_be_explicitly_rolled_back(commercial_client: TestClient) -> None:
    templates = data(commercial_client.get("/v1/admin/industry-templates", headers=headers()))["industry_templates"]
    template = templates[0]
    assert template["capacity_assumptions"]["status"] == "unqualified"
    assert template["acceptance_evidence"]["valid"] is True
    assert len(template["acceptance_evidence"]["combined_sha256"]) == 64
    assert template["acceptance_evidence"]["manifests"][0]["case_count"] >= 2
    assert template["delivery_checklist"]
    preview = data(
        commercial_client.get(
            f"/v1/admin/industry-templates/{template['template_id']}/preview",
            headers=headers(),
        )
    )["preview"]
    applied = data(
        commercial_client.post(
            f"/v1/admin/industry-templates/{template['template_id']}/apply",
            headers=headers(),
            json={"expected_fingerprint": preview["fingerprint"], "dry_run": False},
        )
    )["template_application"]
    application_id = applied["application"]["template_application_id"]
    current = portrait_commercial.get_commercial_profile("tenant-a", "default")
    assert current["template_id"] == template["template_id"]

    rolled_back = data(
        commercial_client.post(
            f"/v1/admin/industry-template-applications/{application_id}/rollback",
            headers=headers(),
            json={"reason": "acceptance sample failed"},
        )
    )["template_rollback"]
    assert rolled_back["application"]["status"] == "rolled_back"
    assert rolled_back["commercial_profile"]["template_id"] is None
    repeated = commercial_client.post(
        f"/v1/admin/industry-template-applications/{application_id}/rollback",
        headers=headers(),
        json={"reason": "repeat"},
    )
    assert repeated.status_code == 409


def test_industry_template_preview_fails_closed_without_acceptance_manifest(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    workspace_tmp_path: Path,
) -> None:
    monkeypatch.setattr(portrait_commercial, "_TEMPLATE_ACCEPTANCE_ROOT", workspace_tmp_path)
    response = commercial_client.get(
        "/v1/admin/industry-templates/campus-safety/preview",
        headers=headers(),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "template_acceptance_evidence_invalid"


def test_runtime_entitlement_enforcement_blocks_unentitled_capability(
    commercial_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import settings

    profile = data(commercial_client.get("/v1/access/projects/default/commercial-profile", headers=headers()))[
        "commercial_profile"
    ]
    data(
        commercial_client.patch(
            "/v1/access/projects/default/commercial-profile",
            headers=headers(),
            json={
                "commercial_status": "active",
                "reason": "contract active",
                "approved_by": "commercial-owner",
                "expected_version": profile["version"],
            },
        )
    )
    data(
        commercial_client.post(
            "/v1/access/entitlements",
            headers=headers(),
            json={
                "allowed_capabilities": ["face_detection"],
                "reason": "compliance-gated entitlement",
                "approved_by": "commercial-owner",
            },
        )
    )
    monkeypatch.setattr(settings, "COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED", True)
    monkeypatch.setattr(settings, "COMMERCIAL_LICENSE_REQUIRED", False)

    for control_id in sorted(portrait_commercial.COMPLIANCE_CONTROL_IDS):
        payload = approved_compliance_payload(control_id)
        portrait_commercial.upsert_compliance_record(
            "tenant-a",
            "default",
            control_id,
            {key: value for key, value in payload.items() if key != "approved_by"},
            actor="privacy-owner",
            approved_by="privacy-owner",
        )

    allowed = portrait_commercial.require_entitlement_capability("tenant-a", "default", "face_detection")
    assert allowed is not None
    with pytest.raises(Exception) as exc_info:
        portrait_commercial.require_entitlement_capability("tenant-a", "default", "pose")
    assert getattr(exc_info.value, "status_code", None) == 403
