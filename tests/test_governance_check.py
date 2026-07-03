from pathlib import Path

from tools.governance_check import run_checks


def test_repository_governance_check_passes_on_repo_root() -> None:
    report = run_checks(Path("."))

    assert report["ok"] is True
    names = {item["name"] for item in report["checks"]}
    assert {"supply_chain", "production_profile", "governance_docs", "burn_rate_alerts", "model_governance_assets", "data_governance_schedule"} <= names
