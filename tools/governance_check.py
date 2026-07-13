"""PortraitHub 仓库级治理检查。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in read_text(path).splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def has_markers(text: str, markers: list[str]) -> bool:
    return all(marker in text for marker in markers)


def check_supply_chain(root: Path) -> dict[str, Any]:
    workflow = root / ".github" / "workflows" / "supply-chain.yml"
    governance = root / ".github" / "workflows" / "governance.yml"
    docs = root / "docs" / "security" / "SUPPLY_CHAIN.md"
    return {
        "name": "supply_chain",
        "ok": workflow.is_file()
        and governance.is_file()
        and has_markers(read_text(workflow), ["anchore/sbom-action", "aquasecurity/trivy-action", "ossf/scorecard-action", "sigstore/cosign-installer", "actions/attest@v4"])
        and has_markers(read_text(governance), ["python tools/governance_check.py", "python tools/model_governance_check.py"])
        and has_markers(read_text(docs), ["SBOM", "SLSA provenance", "cosign", "Trivy", "Scorecard", "Dependabot", "模型构件"]),
        "detail": {"supply_chain_workflow": str(workflow), "governance_workflow": str(governance), "docs": str(docs)},
    }


def check_production_profile(root: Path) -> dict[str, Any]:
    profile = load_env_file(root / "ops" / "production.env.example")
    required = {
        "PORTRAIT_RUNTIME_PROFILE": "production",
        "PRODUCTION_EXTERNAL_SERVICES_REQUIRED": "true",
        "AUTH_REQUIRED": "true",
        "RBAC_ENABLED": "true",
        "ENABLE_API_DOCS": "false",
        "DEBUG_ENDPOINTS_ENABLED": "false",
        "PORTRAIT_STORAGE_BACKEND": "postgres",
        "PORTRAIT_VECTOR_BACKEND": "pgvector",
        "PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND": "true",
        "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES": "true",
        "PORTRAIT_OBJECT_STORAGE_BACKEND": "s3",
        "TASK_QUEUE_BACKEND": "redis",
        "READY_CHECK_DEPENDENCIES": "true",
        "OPENTELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318/v1/traces",
        "REQUIRE_ENCRYPTION": "true",
        "ALLOW_PRIVATE_STREAM_HOSTS": "false",
    }
    return {
        "name": "production_profile",
        "ok": all(profile.get(key) == value for key, value in required.items()),
        "detail": {"path": str(root / "ops" / "production.env.example"), "profile_keys": sorted(profile)},
    }


def check_governance_docs(root: Path) -> dict[str, Any]:
    docs = [
        root / "docs" / "governance" / "AI_MODEL_GOVERNANCE.md",
        root / "docs" / "governance" / "PRIVACY_COMPLIANCE.md",
        root / "docs" / "operations" / "RUNBOOK.md",
        root / "docs" / "operations" / "BURN_RATE_POLICY.md",
    ]
    return {"name": "governance_docs", "ok": all(path.is_file() for path in docs), "detail": {"paths": [str(path) for path in docs]}}


def check_alerts(root: Path) -> dict[str, Any]:
    alerts = read_text(root / "ops" / "prometheus-burnrate-alerts.yml")
    return {
        "name": "burn_rate_alerts",
        "ok": has_markers(alerts, ["PortraitHubFastBurnErrorBudget", "PortraitHubSlowBurnErrorBudget", "runbook_url"]),
        "detail": {"path": str(root / "ops" / "prometheus-burnrate-alerts.yml")},
    }


def check_model_governance_assets(root: Path) -> dict[str, Any]:
    assets = [
        root / "tools" / "model_governance_check.py",
        root / "models" / "yolov8n.governance.yml",
        root / "models" / "osnet_ibn_x1_0.governance.yml",
        root / "tests" / "test_model_governance_check.py",
        root / "tests" / "test_governance_check.py",
    ]
    return {
        "name": "model_governance_assets",
        "ok": all(path.is_file() for path in assets),
        "detail": {"paths": [str(path) for path in assets]},
    }


def check_data_governance_schedule(root: Path) -> dict[str, Any]:
    scheduler = root / "tools" / "portrait_governance_scheduler.py"
    backup_scheduler = root / "tools" / "portrait_backup_scheduler.py"
    service = root / "deploy" / "portrait-governance-scheduler.service"
    timer = root / "deploy" / "portrait-governance-scheduler.timer"
    cronjob = root / "deploy" / "k8s-governance-cronjob.yaml"
    service_text = read_text(service)
    return {
        "name": "data_governance_schedule",
        "ok": scheduler.is_file()
        and backup_scheduler.is_file()
        and service.is_file()
        and timer.is_file()
        and cronjob.is_file()
        and has_markers(read_text(backup_scheduler), ["/v1/admin/backup", "post_backup"])
        and has_markers(read_text(scheduler), ["post_backup", "/v1/admin/retention/cleanup", "run_governance_scheduler"])
        and has_markers(service_text, ["EnvironmentFile=", "portrait_governance_scheduler.py", "--once"])
        and "--token" not in service_text
        and has_markers(read_text(timer), ["OnCalendar=hourly", "Persistent=true"])
        and has_markers(read_text(cronjob), ["kind: CronJob", "portrait_governance_scheduler.py"]),
        "detail": {"scheduler": str(scheduler), "backup_scheduler": str(backup_scheduler), "service": str(service), "timer": str(timer), "cronjob": str(cronjob)},
    }
def run_checks(root: Path) -> dict[str, Any]:
    checks = [
        check_supply_chain(root),
        check_production_profile(root),
        check_governance_docs(root),
        check_alerts(root),
        check_model_governance_assets(root),
        check_data_governance_schedule(root),
    ]
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="运行仓库治理检查。")
    parser.add_argument("--root", default=".", help="项目根目录。")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    args = parser.parse_args()
    report = run_checks(Path(args.root).resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"治理检查：{'通过' if report['ok'] else '失败'}")
        for item in report["checks"]:
            print(f"{'通过' if item['ok'] else '失败'}: {item['name']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
