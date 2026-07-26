from __future__ import annotations

import json
from pathlib import Path

from tools.portrait_upgrade_traceability import validate_traceability

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "requirements" / "COMMERCIAL_REQUIREMENTS.json"


def test_commercial_requirement_cards_are_complete_and_honest() -> None:
    result = validate_traceability(ROOT, MATRIX)

    assert result["ok"] is True, result["errors"]
    assert result["structurally_valid"] is True
    assert result["requirement_count"] == 14
    assert result["release_ready"] is False
    assert result["release_blockers"]


def test_release_mode_blocks_unaccepted_requirements() -> None:
    result = validate_traceability(ROOT, MATRIX, release=True)

    assert result["ok"] is False
    assert result["decision"] == "block"


def test_traceability_rejects_accepted_card_without_approvals(tmp_path: Path) -> None:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    payload["requirements"][0]["status"] = "accepted"
    payload["requirements"][0]["status_history"][-1]["status"] = "accepted"
    payload["requirements"][0]["blockers"] = []
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_traceability(ROOT, path)

    assert result["ok"] is False
    assert any("lacks approvals" in error for error in result["errors"])
