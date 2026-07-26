from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.portrait_kubernetes_release import (
    ReleaseManifestError,
    materialize_release,
    validate_release,
)
from tools.portrait_release_preflight import check_kubernetes_manifest

ROOT = Path(__file__).resolve().parents[1]
K8S_ROOT = ROOT / "deploy" / "kubernetes" / "base"


def resources() -> list[dict[str, Any]]:
    documents = []
    for path in sorted(K8S_ROOT.glob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        documents.extend(
            item
            for item in yaml.safe_load_all(path.read_text(encoding="utf-8"))
            if isinstance(item, dict)
        )
    return documents


def resource(kind: str, name: str) -> dict[str, Any]:
    for item in resources():
        if item.get("kind") == kind and item.get("metadata", {}).get("name") == name:
            return item
    raise AssertionError(f"missing {kind}/{name}")


def test_kustomization_references_existing_resources() -> None:
    kustomization = yaml.safe_load((K8S_ROOT / "kustomization.yaml").read_text(encoding="utf-8"))

    assert kustomization["namespace"] == "portrait-hub"
    assert all((K8S_ROOT / item).is_file() for item in kustomization["resources"])


def test_workload_templates_use_restricted_security_and_resources() -> None:
    deployments = [item for item in resources() if item.get("kind") == "Deployment"]
    names = {item["metadata"]["name"] for item in deployments}
    assert {
        "portrait-hub-api",
        "portrait-hub-api-canary",
        "portrait-hub-gpu-worker",
        "portrait-video-job-worker",
        "portrait-stream-worker",
    } <= names

    for deployment in deployments:
        pod_spec = deployment["spec"]["template"]["spec"]
        assert pod_spec["automountServiceAccountToken"] is False
        assert pod_spec["securityContext"]["runAsNonRoot"] is True
        for container in pod_spec["containers"]:
            assert container["resources"]["requests"]
            assert container["resources"]["limits"]
            assert container["securityContext"]["allowPrivilegeEscalation"] is False
            assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_base_is_a_template_and_release_preflight_rejects_its_placeholders() -> None:
    errors = validate_release(resources())
    preflight = check_kubernetes_manifest(K8S_ROOT)

    assert errors
    assert any("placeholder digest" in error for error in errors)
    assert any("example" in error for error in errors)
    assert preflight["ok"] is False
    assert preflight["errors"]


def test_release_materialization_replaces_images_hostname_and_canary_weight() -> None:
    stable_image = f"registry.corp.internal/portrait-hub@sha256:{'a1' * 32}"
    canary_image = f"registry.corp.internal/portrait-hub@sha256:{'b2' * 32}"

    rendered = materialize_release(
        stable_image=stable_image,
        canary_image=canary_image,
        hostname="portrait.corp.internal",
        canary_weight=7,
    )

    assert validate_release(rendered) == []
    workloads = [item for item in rendered if item.get("kind") in {"Deployment", "Job", "CronJob"}]
    for workload in workloads:
        if workload["kind"] == "CronJob":
            pod_spec = workload["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        else:
            pod_spec = workload["spec"]["template"]["spec"]
        expected = canary_image if workload["metadata"]["name"] == "portrait-hub-api-canary" else stable_image
        assert {container["image"] for container in pod_spec["containers"]} == {expected}
        assert workload["metadata"]["namespace"] == "portrait-hub"

    route = next(item for item in rendered if item.get("kind") == "HTTPRoute")
    assert route["spec"]["hostnames"] == ["portrait.corp.internal"]
    weighted = route["spec"]["rules"][1]["backendRefs"]
    assert {item["name"]: item["weight"] for item in weighted} == {
        "portrait-hub-api": 93,
        "portrait-hub-api-canary": 7,
    }
    assert "example" not in yaml.safe_dump_all(rendered).lower()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"stable_image": f"ghcr.io/example/portrait-hub@sha256:{'a1' * 32}"}, "example repository"),
        ({"canary_image": f"registry.corp/portrait-hub@sha256:{'0' * 64}"}, "placeholder digest"),
        ({"hostname": "portrait.example.com"}, "example domain"),
        ({"canary_weight": 0}, "between 1 and 99"),
    ],
)
def test_release_materialization_rejects_non_deployable_inputs(
    overrides: dict[str, Any],
    message: str,
) -> None:
    arguments: dict[str, Any] = {
        "stable_image": f"registry.corp/portrait-hub@sha256:{'a1' * 32}",
        "canary_image": f"registry.corp/portrait-hub@sha256:{'b2' * 32}",
        "hostname": "portrait.corp.internal",
        "canary_weight": 5,
    }
    arguments.update(overrides)

    with pytest.raises(ReleaseManifestError, match=message):
        materialize_release(**arguments)


def test_api_and_gpu_probe_and_scheduling_contracts() -> None:
    api = resource("Deployment", "portrait-hub-api")["spec"]["template"]["spec"]["containers"][0]
    assert api["startupProbe"]["httpGet"]["path"] == "/health"
    assert api["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert api["livenessProbe"]["httpGet"]["path"] == "/health"

    gpu_spec = resource("Deployment", "portrait-hub-gpu-worker")["spec"]["template"]["spec"]
    gpu = gpu_spec["containers"][0]
    assert gpu_spec["runtimeClassName"] == "nvidia"
    assert gpu_spec["nodeSelector"]["nvidia.com/gpu.present"] == "true"
    assert gpu_spec["tolerations"][0]["key"] == "nvidia.com/gpu"
    assert gpu["resources"]["requests"]["nvidia.com/gpu"] == "1"
    assert gpu["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert gpu["startupProbe"]["httpGet"]["path"] == "/ready"


def test_hpa_keda_availability_network_and_canary_contracts() -> None:
    hpas = {item["metadata"]["name"] for item in resources() if item.get("kind") == "HorizontalPodAutoscaler"}
    assert {"portrait-hub-api", "portrait-hub-gpu-worker", "portrait-stream-worker"} <= hpas

    scaled = resource("ScaledObject", "portrait-video-job-worker")
    assert scaled["spec"]["minReplicaCount"] == 0
    assert scaled["spec"]["maxReplicaCount"] >= 2
    assert scaled["spec"]["triggers"][0]["type"] == "redis-streams"

    assert len([item for item in resources() if item.get("kind") == "PodDisruptionBudget"]) >= 3
    assert len([item for item in resources() if item.get("kind") == "NetworkPolicy"]) >= 2

    route = resource("HTTPRoute", "portrait-hub")
    targeted = route["spec"]["rules"][0]
    headers = {
        header["name"]
        for match in targeted["matches"]
        for header in match.get("headers", [])
    }
    assert headers == {"x-tenant-id", "x-project-id", "x-model-version"}
    weighted = route["spec"]["rules"][1]["backendRefs"]
    assert sum(item["weight"] for item in weighted) == 100
    assert {item["name"] for item in weighted} == {
        "portrait-hub-api",
        "portrait-hub-api-canary",
    }


def test_release_preflight_and_governance_jobs_are_present() -> None:
    preflight = resource("Job", "portrait-release-preflight")
    command = preflight["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command == [
        "python",
        "-m",
        "tools.portrait_release_preflight",
        "--apply-migrations",
        "--check-migrations",
        "--check-models",
        "--prewarm",
    ]
    cronjob = resource("CronJob", "portrait-governance-scheduler")
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"


def test_high_risk_authentication_window_is_explicit() -> None:
    config = resource("ConfigMap", "portrait-hub-config")["data"]

    assert config["STEP_UP_AUTH_MAX_AGE_SECONDS"] == "300"
